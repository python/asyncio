#!/bin/bash

# Script to copy asyncio files to the standard library tree.
# Optional argument is the root of the Python 3.4 tree.
# Assumes you have already created Lib/asyncio and
# Lib/test/test_asyncio in the destination tree.

CPYTHON=${1-$HOME/cpython}

if [ ! -d $CPYTHON ]
then
    echo Bad destination $CPYTHON
    exit 1
fi

if [ ! -f asyncio/__init__.py ]
then
    echo Bad current directory
    exit 1
fi

maybe_copy()
{
    SRC=$1
    DST=$CPYTHON/$2
    if cmp $DST $SRC
    then
        return
    fi
    echo ======== $SRC === $DST ========
    diff -u $DST $SRC
    echo -n "Copy $SRC? [y/N] "
    read X
    case $X in
        [yY]*) echo Copying $SRC; cp $SRC $DST;;
        *) echo Not copying $SRC;;
    esac
}

for i in `(cd asyncio && ls *.py)`
do
    if [ $i == selectors.py ]
    then
        maybe_copy asyncio/$i Lib/$i
    else
        maybe_copy asyncio/$i Lib/asyncio/$i
    fi
done

for i in `(cd tests && ls *.py sample.???)`
do
    maybe_copy tests/$i Lib/test/test_asyncio/$i
done

maybe_copy overlapped.c Modules/overlapped.c
