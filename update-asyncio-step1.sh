set -e -x
git checkout trollius
git pull -u
git checkout master
git pull https://github.com/python/asyncio.git

git checkout trollius
# rename-threshold=25: a similarity of 25% is enough to consider two files
# rename candidates
git merge -X rename-threshold=25 master

echo "Now run ./update-tulip-step2.sh"
