# Tests kambience_scenes.lua with a Lua runtime: pip install lupa; python3 test_scenes.py
import os, tempfile
from lupa import LuaRuntime
here = os.path.dirname(os.path.abspath(__file__))
lua = LuaRuntime()
S = lua.execute(open(os.path.join(here, 'kambience_scenes.lua')).read())

sc = lua.eval('{ {frag=1, at=0, sound="silence"}, {frag=2, at=0, sound="rain/heavy"},'
              ' {frag=2, at=0.5, sound="rain/indoor"}, {frag=4, at=0.2, sound="fire/hearth"} }')
f = lambda fr, at: (S.find(sc, fr, at) or {'sound': None})['sound']
assert f(1, 0.9) == 'silence'
assert f(2, 0.49) == 'rain/heavy' and f(2, 0.5) == 'rain/indoor'
assert f(3, 0.0) == 'rain/indoor' and f(4, 0.1) == 'rain/indoor' and f(4, 0.2) == 'fire/hearth'
assert S.find(lua.eval('{}'), 1, 0) is None

assert S.fraction(15, 10, 20) == 0.5 and S.fraction(5, 10, 20) == 0 and S.fraction(30, 10, 20) == 1
assert S.fraction(10, 10, 10) == 0

d = tempfile.mkdtemp()
os.makedirs(f'{d}/rain')
for p in ['rain/heavy.mp3', 'rain/b-light.ogg']:
    open(f'{d}/{p}', 'w').close()
assert S.resolve(d, 'rain/heavy') == f'{d}/rain/heavy.mp3'
assert S.resolve(d, 'rain/b-light') == f'{d}/rain/b-light.ogg'
assert S.resolve(d, 'rain/on-tent') is None    # missing file is silence, never another rain sound
assert S.resolve(d, 'rain') is None
assert S.resolve(d, 'silence') is None
print('ok')
