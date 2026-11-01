"""Finish admitted OTA blocks at a soft deadline without opening another block.

The verified baseline must come from an owned idle-to-pull session while DATA
is closed and the source cache is empty. Aggregate receiver counts cannot
prove a drain when an unrelated active fetch already has untracked RAM slots.
"""
from __future__ import annotations

import math
import struct
import time


class BlockDrainError(ValueError):
    pass


class BlockDrainSeeder:
    def __init__(self, blob, *, factory=None, clock=time.monotonic, on_change=None):
        if factory is None:
            from ota_wizard_repeater import SeederData
            factory = SeederData
        self.delegate = factory(blob)
        self.info = self.delegate.info
        self.clock, self.on_change = clock, on_change
        self.state = "closed"
        self.baseline = self.deadline = self.required_blocks = None
        self.admitted = set()
        self.blocked_requests = self.payload_bytes = 0
        self.last_verified = None

    def snapshot(self):
        return dict(state=self.state, baseline_blocks=self.baseline,
                    admitted_blocks=sorted(self.admitted), required_blocks=self.required_blocks,
                    last_verified_blocks=self.last_verified, blocked_data_requests=self.blocked_requests,
                    host_payload_bytes=self.payload_bytes)

    def changed(self):
        if self.on_change is not None:
            # Called before a newly admitted DATA response is returned, so a
            # caller can persist its admission journal before USB transmits it.
            try: self.on_change(self.snapshot())
            except BaseException:
                # A retry must not bypass a failed durability barrier merely
                # because the index was added to the in-memory set already.
                self.state = "failed"
                raise

    def arm(self, verified_blocks, *, fresh_resume, deadline=None):
        total = math.ceil(self.info.payload_size / self.info.block_size)
        if (self.state != "closed" or self.baseline is not None or fresh_resume is not True
                or type(verified_blocks) is not int or not 0 <= verified_blocks <= total):
            raise BlockDrainError("A closed, owned resume needs a verified baseline")
        self.baseline = self.last_verified = verified_blocks
        self.deadline = deadline
        self.state = "open"
        self.changed()
        self.check_deadline()

    def freeze(self):
        if self.state == "open":
            self.state = "draining"
            self.required_blocks = self.baseline + len(self.admitted)
            self.changed()
        return self.required_blocks

    def restore_owned(self, snapshot, *, deadline=None):
        """Restore a journal whose receiver/package/owner scope was validated."""
        total = math.ceil(self.info.payload_size / self.info.block_size)
        baseline = snapshot.get("baseline_blocks")
        blocks = snapshot.get("admitted_blocks")
        count = snapshot.get("last_verified_blocks")
        state = snapshot.get("state")
        if (self.baseline is not None or type(baseline) is not int or not 0 <= baseline <= total
                or not isinstance(blocks, list) or any(type(b) is not int or not 0 <= b < total for b in blocks)
                or len(set(blocks)) != len(blocks) or baseline + len(blocks) > total
                or type(count) is not int or not baseline <= count <= baseline + len(blocks)
                or state not in ("open", "draining", "closed")):
            raise BlockDrainError("Invalid owned block admission journal")
        self.baseline, self.last_verified = baseline, count
        self.admitted = set(blocks)
        self.deadline = deadline
        self.state = "open" if state == "open" else "draining"
        if self.state == "draining": self.required_blocks = baseline + len(blocks)
        self.changed()
        self.check_deadline()

    def check_deadline(self):
        if self.state == "open" and self.deadline is not None and self.clock() >= self.deadline:
            self.freeze()

    def close(self):
        self.state = "closed"
        self.changed()

    def observe_verified(self, count):
        """Only a receiver's validated, matching-MID FETCH count belongs here."""
        if self.baseline is None or type(count) is not int:
            raise BlockDrainError("No verified baseline")
        maximum = self.baseline + len(self.admitted)
        if count < self.last_verified or count > maximum:
            raise BlockDrainError("Receiver progress includes untracked or lost blocks")
        self.last_verified = count
        return self.state == "draining" and count == self.required_blocks

    def blocked(self):
        self.blocked_requests += 1
        return 1, b""

    def can_serve(self, block):
        self.check_deadline()
        return self.state == "open" or (self.state == "draining" and block in self.admitted)

    def admit(self, block):
        # Recheck after compression/read work. An event loop can be awaiting
        # a slow CLI command at cutoff; DATA itself enforces the deadline.
        if not self.can_serve(block): return False
        if block not in self.admitted:
            total = math.ceil(self.info.payload_size / self.info.block_size)
            if self.baseline + len(self.admitted) >= total:
                raise BlockDrainError("More DATA indices than missing receiver blocks")
            self.admitted.add(block)
            self.changed()
        return True

    def request(self, op, args):
        info = self.info
        if op == 3 and len(args) == 7:
            index, offset, length = struct.unpack("<BIH", args)
            if index == 0 and 0 < length <= 192 and offset + length <= len(self.delegate.blob):
                left = max(offset, info.payload_offset)
                right = min(offset + length, info.payload_offset + info.payload_size)
                if left < right:
                    first = (left - info.payload_offset) // info.block_size
                    last = (right - 1 - info.payload_offset) // info.block_size
                    if first != last or not self.can_serve(first): return self.blocked()
                    status, data = self.delegate.request(op, args)
                    if status == 0:
                        if not self.admit(first): return self.blocked()
                        self.payload_bytes += right - left
                    return status, data
        if op == 9 and len(args) == 7:
            index, block, offset, length = struct.unpack("<BHHH", args)
            total = math.ceil(info.payload_size / info.block_size)
            if index == 0 and block < total and length <= 190 and (length or not offset):
                # Compressed-size probes carry no DATA and must neither open
                # a block nor prevent a subsequent raw fallback.
                if not length: return self.delegate.request(op, args)
                if not self.can_serve(block): return self.blocked()
                status, data = self.delegate.request(op, args)
                if status == 0:
                    if not self.admit(block): return self.blocked()
                    self.payload_bytes += len(data) - 2
                return status, data
        # Manifest and Merkle proof metadata remain readable throughout the
        # drain. The delegate implements only read-only seeder operations.
        return self.delegate.request(op, args)
