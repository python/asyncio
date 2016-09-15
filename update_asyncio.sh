#!/bin/bash

PYTHON=${1-$HOME/cpython}

if [ ! -d $PYTHON ]
then
    echo Bad destination $PYTHON
    exit 1
fi

if [ ! -f asyncio/__init__.py ]
then
    echo Bad current directory
    exit 1
fi

echo "Sync from $PYTHON to $ASYNCIO"
set -e -x
echo

cp $PYTHON/Lib/asyncio/*.py asyncio/
cp $PYTHON/Lib/test/test_asyncio/test_*.py tests/
echo

git status
