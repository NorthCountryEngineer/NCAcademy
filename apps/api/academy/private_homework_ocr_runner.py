"""Apply resource limits and replace this child process with fixed local OCR."""

import os
import resource


if __name__ == "__main__":
    # The caller runs this in a clean interpreter with anonymous pipes. Set
    # limits here instead of using a preexec callback in the threaded API.
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    os.execv(
        "/usr/bin/tesseract",
        ["/usr/bin/tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6", "--dpi", "150"],
    )
