"""Small insertion-ordered caches; eviction releases references to model tensors."""
from collections import OrderedDict

class BoundedCache(OrderedDict):
    def __init__(self, maximum=16):
        super().__init__()
        self.maximum = maximum
    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.move_to_end(key)
        while len(self) > self.maximum:
            self.popitem(last=False)
