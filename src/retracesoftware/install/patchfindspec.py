import os
from pathlib import Path

# Set to track logged modules (avoid duplicates)
logged_modules = set()

class patch_find_spec:
    """
    Hook that logs accessed module file paths to a file.
    One absolute path per line.
    """
    def __init__(self, output_file):
        self.output_file = output_file

    def __call__(self, spec):
        if spec is not None and spec.origin and spec.origin != "built-in" and os.path.isfile(spec.origin):
            module_name = spec.name
            if module_name not in logged_modules:
                # Write absolute path to output file
                abs_path = os.path.realpath(spec.origin)
                self.output_file.write(abs_path + '\n')
                self.output_file.flush()
                logged_modules.add(module_name)
