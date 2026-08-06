"""openwakeword loads .tflite models via `import tflite_runtime.interpreter`,
but tflite-runtime has no wheel for Python 3.14 on macOS. ai-edge-litert is the
maintained successor with the same Interpreter API, so alias it under the old
module name before openwakeword goes looking for it."""

import sys
import types


def install():
    try:
        import tflite_runtime.interpreter  # noqa: F401
        return
    except ModuleNotFoundError:
        pass

    from ai_edge_litert import interpreter

    package = types.ModuleType("tflite_runtime")
    package.interpreter = interpreter
    sys.modules["tflite_runtime"] = package
    sys.modules["tflite_runtime.interpreter"] = interpreter
