

class RuntimeMan:
    def __init__(self):
        self._running = True


    def is_run(self):
        return self._running
    

    def stop(self):
        self._running = False

    def start(self):
        self._running = True





runtime_man = RuntimeMan()