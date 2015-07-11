set -e -x
git checkout trollius
git pull -u
git checkout master
git pull https://github.com/python/asyncio.git
git checkout trollius
git merge master
echo "Now run ./update-tulip-step2.sh"
