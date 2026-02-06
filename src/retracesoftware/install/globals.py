class RecordingPath:
    def __init__(self, path):
        self.path = path
        self.subprocess_counter = 0
        self.fork_counter = 0
    
    def next_subprocess_path(self):
        path = self.path / f'subprocess-{self.subprocess_counter}'
        self.subprocess_counter = self.subprocess_counter + 1
        return path

    def next_fork_path(self):
        path = self.path / f'fork-{self.fork_counter}'
        self.fork_counter = self.fork_counter + 1
        return path

recording_path = None