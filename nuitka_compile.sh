#!/bin/bash

if [ -d "beetent_trimble" ]; then
	echo "Output dir beetent_trimble exists. Please remove it before"
	echo "building a new one."
	exit 1
fi

python -m nuitka  maketentgrid.py --enable-plugin=tk-inter -o maketentgrid.exe --follow-imports  --standalone

if [ "$?" -eq 0 ]; then
	mv maketentgrid.dist beetent_trimble
fi


echo Moved maketentgrid.dist to beetent_trimble.  Zip that up to distribute.
