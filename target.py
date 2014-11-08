from pixie.vm.compiler import compile, with_ns, NS_VAR
from pixie.vm.reader import StringReader, read, eof, PromptReader, MetaDataReader
from pixie.vm.interpreter import interpret
from rpython.jit.codewriter.policy import JitPolicy
from rpython.rlib.jit import JitHookInterface, Counters
from rpython.rlib.rfile import create_stdio
from rpython.annotator.policy import AnnotatorPolicy
from pixie.vm.code import wrap_fn, NativeFn, intern_var
from pixie.vm.stacklet import with_stacklets
import pixie.vm.stacklet as stacklet
from pixie.vm.object import RuntimeException, WrappedException
from rpython.translator.platform import platform
from pixie.vm.primitives import nil
import sys
import os

class DebugIFace(JitHookInterface):
    def on_abort(self, reason, jitdriver, greenkey, greenkey_repr, logops, operations):
        print "Aborted Trace, reason: ", Counters.counter_names[reason], logops, greenkey_repr

import sys, pdb

class Policy(JitPolicy, AnnotatorPolicy):
    def __init__(self):
        JitPolicy.__init__(self, DebugIFace())

def jitpolicy(driver):
    return JitPolicy(jithookiface=DebugIFace())


PROGRAM_ARGUMENTS = intern_var(u"pixie.stdlib", u"program-arguments")
PROGRAM_ARGUMENTS.set_root(nil)

STAR_1 = intern_var(u"pixie.stdlib", u"*1")
STAR_1.set_root(nil)
STAR_2 = intern_var(u"pixie.stdlib", u"*2")
STAR_2.set_root(nil)
STAR_3 = intern_var(u"pixie.stdlib", u"*3")
STAR_3.set_root(nil)
STAR_E = intern_var(u"pixie.stdlib", u"*e")
STAR_E.set_root(nil)

class ReplFn(NativeFn):
    def __init__(self, args):
        self._argv = args

    def inner_invoke(self, args):
        from pixie.vm.keyword import keyword
        import pixie.vm.rt as rt
        from pixie.vm.string import String
        import pixie.vm.persistent_vector as vector

        print "Pixie 0.1 - Interactive REPL"
        print "(" + platform.name + ", " + platform.cc + ")"
        print "----------------------------"

        with with_ns(u"user"):
            NS_VAR.deref().include_stdlib()

        acc = vector.EMPTY
        for x in self._argv:
            acc = rt.conj(acc, rt.wrap(x))

        PROGRAM_ARGUMENTS.set_root(acc)

        rdr = MetaDataReader(PromptReader())
        with with_ns(u"user"):
            while True:
                try:
                    val = read(rdr, False)
                    if val is eof:
                        break
                    val = interpret(compile(val))
                    self.set_recent_vars(val)
                except WrappedException as ex:
                    print "Error: ", ex._ex.__repr__()
                    rdr.reset_line()
                    self.set_error_var(ex._ex)
                    continue
                if val is keyword(u"exit-repl"):
                    break
                val = rt.str(val)
                assert isinstance(val, String), "str should always return a string"
                print val._str

    def set_recent_vars(self, val):
        if rt.eq(val, STAR_1.deref()):
            return
        STAR_3.set_root(STAR_2.deref())
        STAR_2.set_root(STAR_1.deref())
        STAR_1.set_root(val)

    def set_error_var(self, ex):
        STAR_E.set_root(ex)

class BatchModeFn(NativeFn):
    def __init__(self, args):
        self._file = args[0]
        self._argv = args[1:]

    def inner_invoke(self, args):
        import pixie.vm.rt as rt
        import pixie.vm.persistent_vector as vector

        with with_ns(u"user"):
            NS_VAR.deref().include_stdlib()

        acc = vector.EMPTY
        for x in self._argv:
            acc = rt.conj(acc, rt.wrap(x))

        PROGRAM_ARGUMENTS.set_root(acc)

        with with_ns(u"user"):
            try:
                if self._file == '-':
                    stdin, _, _ = create_stdio()
                    code = stdin.read()
                    interpret(compile(read(StringReader(unicode(code)), True)))
                else:
                    rt.load_file(rt.wrap(self._file))
            except WrappedException as ex:
                print "Error: ", ex._ex.__repr__()
                os._exit(1)

class EvalFn(NativeFn):
    def __init__(self, expr):
        self._expr = expr

    def inner_invoke(self, args):
        import pixie.vm.rt as rt

        with with_ns(u"user"):
            NS_VAR.deref().include_stdlib()

            interpret(compile(read(StringReader(unicode(self._expr)), True)))

@wrap_fn
def run_load_stdlib():
    import pixie.vm.compiler as compiler
    import pixie.vm.reader as reader
    f = open("pixie/stdlib.lisp")
    data = f.read()
    f.close()
    rdr = reader.MetaDataReader(reader.StringReader(unicode(data)), u"pixie/stdlib.pixie")
    result = nil

    with compiler.with_ns(u"pixie.stdlib"):
        while True:
            form = reader.read(rdr, False)
            if form is reader.eof:
                return result
            result = compiler.compile(form).invoke([])

def load_stdlib():







    stacklet.with_stacklets(run_load_stdlib)

def entry_point(args):
    interactive = True
    script_args = []

    load_stdlib()

    i = 1
    while i < len(args):
        arg = args[i]

        if arg.startswith('-') and arg != '-':
            if arg == '-v' or arg == '--version':
                print "Pixie 0.1"
                return 0
            elif arg == '-h' or arg == '--help':
                print args[0] + " [<options>] [<file>]"
                print "  -h, --help     print this help"
                print "  -v, --version  print the version number"
                print "  -e, --eval     evaluate the given expression"
                return 0
            elif arg == '-e' or arg == '--eval':
                i += 1
                if i < len(args):
                    expr = args[i]
                    with_stacklets(EvalFn(expr))
                    return 0
                else:
                    print "Expected argument for " + arg
                    return 1
            else:
                print "Unknown option " + arg
                return 1
        else:
            interactive = False
            script_args = args[i:]
            break

        i += 1

    if interactive:
        with_stacklets(ReplFn(args))
    else:
        with_stacklets(BatchModeFn(script_args))

    return 0



from rpython.rtyper.lltypesystem import lltype
from rpython.jit.metainterp import warmspot

def run_child(glob, loc):
    interp = loc['interp']
    graph = loc['graph']
    interp.malloc_check = False

    def returns_null(T, *args, **kwds):
        return lltype.nullptr(T)
    interp.heap.malloc_nonmovable = returns_null     # XXX

    from rpython.jit.backend.llgraph.runner import LLGraphCPU
    #LLtypeCPU.supports_floats = False     # for now
    apply_jit(interp, graph, LLGraphCPU)


def apply_jit(interp, graph, CPUClass):
    print 'warmspot.jittify_and_run() started...'
    policy = Policy()
    warmspot.jittify_and_run(interp, graph, [], policy=policy,
                             listops=True, CPUClass=CPUClass,
                             backendopt=True, inline=True)

def run_debug(argv):
    from rpython.rtyper.test.test_llinterp import get_interpreter

    # first annotate and rtype
    try:
        interp, graph = get_interpreter(entry_point, [], backendopt=False,
                                        #config=config,
                                        #type_system=config.translation.type_system,
                                        policy=Policy())
    except Exception, e:
        print '%s: %s' % (e.__class__, e)
        pdb.post_mortem(sys.exc_info()[2])
        raise

    # parent process loop: spawn a child, wait for the child to finish,
    # print a message, and restart
    #unixcheckpoint.restartable_point(auto='run')

    from rpython.jit.codewriter.codewriter import CodeWriter
    CodeWriter.debug = True
    run_child(globals(), locals())

import pixie.vm.rt as rt
rt.init()
#stacklet.global_state = stacklet.GlobalState()

def target(*args):
    import pixie.vm.rt as rt
    driver = args[0]
    driver.exe_name = "pixie-vm"
    rt.__config__ = args[0].config




    return entry_point, None

import rpython.config.translationoption
print rpython.config.translationoption.get_combined_translation_config()

if __name__ == "__main__":
    entry_point(sys.argv)
