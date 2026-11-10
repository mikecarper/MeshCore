These unmodified Sensirion Core 0.7.3 files exercise the build-time IntegerType
rename using the actual sensor decoder. Their BSD license and copyright notices
remain in each source file. Upstream: https://github.com/Sensirion/arduino-core

Only the receive decoder and its error declarations are needed for the host
regression. The production overlay copies the complete installed src tree.
