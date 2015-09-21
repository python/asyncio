PYTHON=~/prog/python/default
ASYNCIO=.

echo "Sync from $PYTHON to $ASYNCIO"
set -e -x
echo

cp $PYTHON/Lib/asyncio/*.py asyncio/
cp $PYTHON/Lib/test/test_asyncio/test_*.py tests/
echo

git status
