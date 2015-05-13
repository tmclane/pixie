from pixie.vm2.object import Object, Type, Continuation, stack_cons
from pixie.vm2.primitives import nil, false
from pixie.vm2.array import Array
import pixie.vm2.code as code
from pixie.vm2.code import BaseCode

import rpython.rlib.jit as jit

class AST(Object):
    _immutable_fields_ = ["_c_meta"]
    def __init__(self, meta):
        self._c_meta = meta

    def get_short_location(self):
        if self._c_meta != nil:
            return self._c_meta.get_short_location()
        return "<unknown>"

class Meta(Object):
    _type = Type(u"pixie.stdlib.Meta")
    _immutable_fields_ = ["_c_column_number", "_c_line_tuple"]
    def __init__(self, line_tuple, column_number):
        self._c_column_number = column_number
        self._c_line_tuple = line_tuple

    def get_short_location(self):
        (line, file, line_number) = self._c_line_tuple

        return str(file) + " @ " + str(line[:self._c_column_number]) + "^" + str(line[self._c_column_number:])

class PrevASTNil(AST):
    def __init__(self):
        AST.__init__(self, nil)

class InterpretK(Continuation):
    _immutable_ = True
    def __init__(self, ast, locals):
        self._c_ast = ast
        self._c_locals = locals

    def call_continuation(self, val, stack):
        ast = jit.promote(self._c_ast)
        return ast.interpret(val, self._c_locals, stack)

    def get_ast(self):
        return self._c_ast

class Const(AST):
    _immutable_fields_ = ["_c_val"]
    _type = Type(u"pixie.interpreter.Const")
    def __init__(self, val, meta=nil):
        AST.__init__(self, meta)
        self._c_val = val

    def interpret(self, _, locals, stack):
        return jit.promote(self._c_val), stack

class Locals(object):
    _immutable_ = True
    def __init__(self, name, value, next):
        self._c_value = value
        self._c_name = name
        self._c_next = next

    @staticmethod
    @jit.unroll_safe
    def get_local(self, name):
        if self is None:
            return nil
        c = self
        while c._c_name is not name:
            c = c._c_next
        return c._c_value

class Lookup(AST):
    _immutable_fields_ = ["_c_name"]
    def __init__(self, name, meta=nil):
        AST.__init__(self, meta)
        self._c_name = name

    def interpret(self, _, locals, stack):
        return Locals.get_local(locals, self._c_name), stack


class Fn(AST):
    _immutable_fields_ = ["_c_name", "_c_args", "_c_body", "_c_closed_overs"]
    def __init__(self, name, args, body, closed_overs=[], meta=nil):
        AST.__init__(self, meta)
        self._c_name = name
        self._c_args = args
        self._c_body = body
        self._c_closed_overs = closed_overs

    @jit.unroll_safe
    def interpret(self, _, locals, stack):
        locals_prefix = None
        for n in self._c_closed_overs:
            locals_prefix = Locals(n, Locals.get_local(locals, n), locals_prefix)

        return InterpretedFn(self._c_name, self._c_args, locals_prefix, self._c_body), stack

class InterpretedFn(code.BaseCode):
    _immutable_fields_ = ["_c_arg_names[*]", "_c_locals", "_c_fn_ast", "_c_name"]
    def __init__(self, name, arg_names, locals_prefix, ast):
        self._c_arg_names = arg_names
        self._c_name = name
        if name is not nil:
            self._c_locals = Locals(name, self, locals_prefix)
        else:
            self._c_locals = locals_prefix
        self._c_fn_ast = ast

    def invoke_k(self, args, stack):
        return self.invoke_k_with(args, stack, self)

    @jit.unroll_safe
    def invoke_k_with(self, args, stack, self_fn):
        # TODO: Check arg count
        locals = Locals(jit.promote(self._c_name), self_fn, self._c_locals)
        arg_names = jit.promote(self._c_arg_names)
        for idx in range(len(arg_names)):
            locals = Locals(arg_names[idx], args[idx], locals)

        return nil, stack_cons(stack, InterpretK(self._c_fn_ast, locals))



class Invoke(AST):
    _immutable_fields_ = ["_c_args", "_c_fn"]
    def __init__(self, args, meta=nil):
        AST.__init__(self, meta)
        self._c_args = args

    def interpret(self, _, locals, stack):
        stack = stack_cons(stack, InvokeK(self))
        stack = stack_cons(stack, ResolveAllK(self._c_args, locals, []))
        stack = stack_cons(stack, InterpretK(self._c_args[0], locals))
        return nil, stack

class InvokeK(Continuation):
    _immutable_ = True
    def __init__(self, ast):
        self._c_ast = ast

    def call_continuation(self, val, stack):
        assert isinstance(val, Array)
        fn = val._list[0]
        args = val._list[1:]
        return fn.invoke_k(args, stack)

    def get_ast(self):
        return self._c_ast



class TailCall(AST):
    _immutable_fields_ = ["_c_args", "_c_fn"]
    def __init__(self, args, meta=nil):
        AST.__init__(self, meta)
        self._c_args = args

    def interpret(self, _, locals, stack):
        stack = stack_cons(stack, TailCallK(self))
        stack = stack_cons(stack, ResolveAllK(self._c_args, locals, []))
        stack = stack_cons(stack, InterpretK(self._c_args[0], locals))
        return nil, stack

class TailCallK(InvokeK):
    _immutable_ = True
    should_enter_jit = True
    def __init__(self, ast):
        InvokeK.__init__(self, ast)

    def get_ast(self):
        return self._c_ast

class ResolveAllK(Continuation):
    _immutable_ = True
    def __init__(self, args, locals, acc):
        self._c_args = args
        self._c_locals = locals
        self._c_acc = acc

    @jit.unroll_safe
    def append_to_acc(self, val):
        acc = [None] * (len(self._c_acc) + 1)
        for x in range(len(self._c_acc)):
            acc[x] = self._c_acc[x]
        acc[len(self._c_acc)] = val
        return acc

    def call_continuation(self, val, stack):
        if len(self._c_acc) + 1 < len(self._c_args):
            stack = stack_cons(stack, ResolveAllK(self._c_args, self._c_locals, self.append_to_acc(val)))
            stack = stack_cons(stack, InterpretK(self._c_args[len(self._c_acc) + 1], self._c_locals))
        else:
            return Array(self.append_to_acc(val)), stack

        return nil, stack

    def get_ast(self):
        return self._c_args[len(self._c_acc)]

class Let(AST):
    _immutable_fields_ = ["_c_names", "_c_bindings", "_c_body"]
    def __init__(self, names, bindings, body, meta):
        AST.__init__(self, meta)
        self._c_names = names
        self._c_bindings = bindings
        self._c_body = body

    def interpret(self, _, locals, stack):
        stack = stack_cons(stack, LetK(self, 0, locals))
        stack = stack_cons(stack, InterpretK(self._c_bindings[0], locals))
        return nil, stack

class LetK(Continuation):
    def __init__(self, ast, idx, locals):
        self._c_idx = idx
        self._c_ast = ast
        self._c_locals = locals

    def call_continuation(self, val, stack):
        assert isinstance(self._c_ast, Let)
        new_locals = Locals(self._c_ast._c_names[self._c_idx], val, self._c_locals)

        if self._c_idx + 1 < len(self._c_ast._c_names):
            stack = stack_cons(stack, LetK(self._c_ast, self._c_idx + 1, new_locals))
            stack = stack_cons(stack, InterpretK(self._c_ast._c_bindings[self._c_idx + 1], new_locals))
        else:
            stack = stack_cons(stack, InterpretK(self._c_ast._c_body, new_locals))

        return nil, stack


    def get_ast(self):
        return self._c_ast

class If(AST):
    _immutable_fields_ = ["_c_test", "_c_then", "_c_else"]
    def __init__(self, test, then, els, meta=nil):
        AST.__init__(self, meta)
        self._c_test = test
        self._c_then = then
        self._c_else = els

    def interpret(self, val, locals, stack):
        stack = stack_cons(stack, IfK(self, locals))
        stack = stack_cons(stack, InterpretK(self._c_test, locals))
        return nil, stack

class IfK(Continuation):
    _immutable_ = True
    def __init__(self, ast, locals):
        self._c_ast = ast
        self._c_locals = locals

    def call_continuation(self, val, stack):
        ast = self._c_ast
        assert isinstance(self._c_ast, If)
        if val is nil or val is false:
            stack = stack_cons(stack, InterpretK(ast._c_else, self._c_locals))
        else:
            stack = stack_cons(stack, InterpretK(ast._c_then, self._c_locals))
        return nil, stack

    def get_ast(self):
        return self._c_ast


class Do(AST):
    _immutable_fields_ = ["_c_body_asts"]
    def __init__(self, args, meta=nil):
        AST.__init__(self, meta)
        self._c_body_asts = args

    @jit.unroll_safe
    def interpret(self, val, locals, stack):
        return nil, stack_cons(stack, DoK(self, self._c_body_asts, locals))


class DoK(Continuation):
    _immutable_ = True
    def __init__(self, ast, do_asts, locals, idx=0):
        self._c_ast = ast
        self._c_locals = locals
        self._c_body_asts = do_asts
        self._c_idx = idx

    def call_continuation(self, val, stack):
        if self._c_idx + 1 < len(self._c_body_asts):
            stack = stack_cons(stack, DoK(self._c_ast, self._c_body_asts, self._c_locals, self._c_idx + 1))
            stack = stack_cons(stack, InterpretK(self._c_body_asts[self._c_idx], self._c_locals))
            return nil, stack
        else:
            stack = stack_cons(stack, InterpretK(self._c_body_asts[self._c_idx], self._c_locals))
            return nil, stack

    def get_ast(self):
        return self._c_ast

class VDeref(AST):
    _immutable_fields_ = ["_c_var"]
    def __init__(self, var, meta=nil):
        AST.__init__(self, meta)
        self._c_var = var

    def interpret(self, val, locals, stack):
        return self._c_var.deref(), stack
