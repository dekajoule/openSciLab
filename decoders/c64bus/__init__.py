##
## This file is part of the openSciLab project.
##
## C64 / 6502 system bus decoder for sigrok.
##
## Copyright (C) 2026 Julian Decker
##
## SPDX-License-Identifier: GPL-2.0-or-later
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 2 of the License, or
## (at your option) any later version.
##

'''
Decodes the system bus of a 6502/6510 or W65C02S computer (address, data, R/W,
optionally SYNC) and labels the memory regions of the Commodore 64 or of Ben
Eater's 6502 computer.
'''

from .pd import Decoder
