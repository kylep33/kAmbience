-- Pure logic with no KOReader dependencies, so test_scenes.py can test it.
local M = {}

M.EXTS = { ".mp3", ".flac", ".wav", ".ogg", ".m4a" }

-- Last scene at or before (frag, at). Scenes are in reading order.
function M.find(scenes, frag, at)
    local found
    for _, s in ipairs(scenes) do
        if s.frag < frag or (s.frag == frag and s.at <= at) then found = s else break end
    end
    return found
end

-- Fraction through the current fragment, from page numbers.
function M.fraction(cur, start, next_start)
    if not start or not next_start or next_start <= start then return 0 end
    local f = (cur - start) / (next_start - start)
    if f < 0 then return 0 elseif f > 1 then return 1 end
    return f
end

local function exists(path)
    local f = io.open(path, "rb")
    if f then f:close() return true end
end

-- sounds/<label>.<ext>, or nil (silence). No guessing from other files: a wrong sound is
-- worse than none.
function M.resolve(dir, label)
    if not label or label == "silence" then return nil end
    for _, e in ipairs(M.EXTS) do
        if exists(dir .. "/" .. label .. e) then return dir .. "/" .. label .. e end
    end
end

return M
