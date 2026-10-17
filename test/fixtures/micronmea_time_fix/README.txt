MicroNMEA 2.0.6 regression fixtures
=================================
The src/MicroNMEA.cpp and src/MicroNMEA.h files are exact original sources from
the project's existing stevemarple/MicroNMEA 2.0.6 native dependency.
Author: Steve Marple <stevemarple@googlemail.com>
Upstream: https://github.com/stevemarple/MicroNMEA
License: GNU Lesser General Public License version 2.1, included in LICENSE.
Only the license file's trailing blank line was omitted; its text is unchanged.

Original source SHA-256:
925da47a9aaa75c43c786ffb5a6530544283dda5138cd7792c4e8f79c8238377
Original header SHA-256:
d87810b25ff920fe5810b8bf7be17c69eb608853407be9da82214ef5616f1fb2

runtime.cpp is MeshCore test code. It compiles the actual complete dependency
against the existing portable Arduino/Stream mocks. It does not reproduce a
simplified parser. The patched dependency source is generated privately using
scripts/micronmea_time_fix.py; the original fixtures remain unchanged.
