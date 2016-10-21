from ipykernel.kernelapp import IPKernelApp
from ipykernel.zmqshell import ZMQInteractiveShell
from IPython.core.autocall import ZMQExitAutocall

import s4ils



class KeepAlive(ZMQExitAutocall):
    def __call__(self, *args, **kwargs):
        super().__call__(True)

keep_alive = KeepAlive()


def patch_exiter():
    if ZMQInteractiveShell.exiter is not keep_alive:
        ZMQInteractiveShell.exiter = keep_alive


def ipy_kernel(server):
    patch_exiter()
    user_ns = {
        'server': server,
    }
    server.kernel_app = IPKernelApp()
    server.kernel_instance = server.kernel_app.instance(user_ns=user_ns)
    server.kernel_instance.initialize([])
    server.kernel_instance.start()
