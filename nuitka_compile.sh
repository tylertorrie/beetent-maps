#!/bin/bash

python -m nuitka  maketentgrid.py --enable-plugin=tk-inter -o maketentgrid.exe --follow-imports  --standalone

if [ "$?" -eq 0 ]; then
	mv maketentgrid.dist beetent_trimble
fi

