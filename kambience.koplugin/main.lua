--[[ kambience: ambient sounds that follow the story.
Reads <book>.kambience.json (made by kambience). Sounds are off until you turn them on for
that book; then, on page turns, the KinAMP daemon (started in the background, never opened by
hand) loops the sound for the current scene over Bluetooth. ]]
local WidgetContainer = require("ui/widget/container/widgetcontainer")
local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local Dispatcher = require("dispatcher")
local json = require("json")
local logger = require("logger")
local ffi = require("ffi")
local C = ffi.C
require("ffi/posix_h")
local Scenes = require("kambience_scenes")
local _ = require("gettext")

-- Hand-copied sounds first, then the KPM sound packs (kept in place: copying would store them twice).
local SOUND_DIRS = { "/mnt/us/kambience/sounds" }
for i = 1, 3 do SOUND_DIRS[#SOUND_DIRS + 1] = "/mnt/us/kmc/kpm/packages/kambience-sounds-" .. i .. "/sounds" end
local KINAMP = "/mnt/us/KinAMP/startkinamp_koreader.sh"
local FIFO = "/tmp/kinamp_cmd"
local PLAYLIST = "/tmp/kambience.m3u"
local FADE_SECONDS, FADE_STEPS = 4, 8
local BTFD = "com.lab126.btfd"
local BT_WAIT = 10 -- seconds to wait for headphones to reconnect

local Kambience = WidgetContainer:extend{
    name = "kambience",
    is_doc_only = true,
}

function Kambience:init()
    self.settings = G_reader_settings:readSetting("kambience", { volume = 40 })
    G_reader_settings:saveSetting("kambience", self.settings) -- menu edits mutate this table in place
    self:onDispatcherRegisterActions()
    self.ui.menu:registerToMainMenu(self)
end

-- Lets people bind "Toggle kambience" to a tap or swipe in KOReader's gesture settings.
function Kambience:onDispatcherRegisterActions()
    Dispatcher:registerAction("kambience_toggle", {
        category = "none", event = "KambienceToggle", title = _("Toggle kambience"), reader = true,
    })
end

---------- KinAMP control ----------

-- Same non-blocking FIFO write KinAMP's own plugin uses; fails fast if no daemon.
local function send(cmds)
    local fd = C.open(FIFO, bit.bor(C.O_WRONLY, C.O_NONBLOCK))
    if fd < 0 then return false end
    local payload = table.concat(cmds, "\n") .. "\n"
    C.write(fd, payload, #payload)
    C.close(fd)
    return true
end

local function running()
    local fd = C.open(FIFO, bit.bor(C.O_WRONLY, C.O_NONBLOCK))
    if fd < 0 then return false end
    C.close(fd)
    return true
end

-- Steps KinAMP's volume to target over FADE_SECONDS. A newer fade cancels an older one.
function Kambience:fadeTo(target, done)
    self.fade_id = (self.fade_id or 0) + 1
    local id, from = self.fade_id, self.vol_now or 0
    for i = 1, FADE_STEPS do
        UIManager:scheduleIn(FADE_SECONDS * i / FADE_STEPS, function()
            if id ~= self.fade_id then return end
            self.vol_now = math.floor(from + (target - from) * i / FADE_STEPS + 0.5)
            send({ "vol " .. self.vol_now })
            if i == FADE_STEPS and done then done() end
        end)
    end
end

-- After launching the daemon, waits (up to ~10 s) for its command pipe.
function Kambience:whenRunning(fn, tries)
    tries = tries or 20
    if running() then return fn() end
    if tries > 0 then UIManager:scheduleIn(0.5, function() self:whenRunning(fn, tries - 1) end) end
end

-- Fade out whatever plays, switch files at volume 0, fade the new one in.
function Kambience:play(path)
    local f = io.open(PLAYLIST, "w")
    if not f then return end
    f:write(path, "\n")
    f:close()
    local function start()
        send({ "vol 0", "strategy 1", "load " .. PLAYLIST, "index 0" })
        self.vol_now = 0
        self:fadeTo(self.settings.volume)
    end
    if running() then
        if (self.vol_now or 0) > 0 then self:fadeTo(0, start) else start() end
    else
        -- --idle: the daemon starts stopped, so it can't blast its saved volume before our fade-in.
        os.execute(string.format("nohup %q --idle >/dev/null 2>&1 &", KINAMP))
        self:whenRunning(start)
    end
end

function Kambience:fadeOut()
    if (self.vol_now or 0) == 0 then return send({ "stop" }) end
    self:fadeTo(0, function() send({ "stop" }) end)
end

function Kambience:stop()
    self.current = nil
    self:fadeOut()
end

---------- Bluetooth ----------

-- "off", "none", the connected device's name, or nil when this Kindle can't be asked
-- (btfd LIPC, the same calls KinAMP uses; KinAMP notes newer models may not have btfd).
local function bt_status(turn_on)
    local ok, lipc = pcall(require, "libopenlipclua")
    if not ok then return nil end
    local ok2, h = pcall(lipc.open_no_name)
    if not ok2 or not h then return nil end
    local status
    local ok3, state = pcall(h.get_int_property, h, BTFD, "BTstate")
    if ok3 and state == 0 then
        status = "off"
        -- Radio on; paired headphones that are switched on normally reconnect by themselves.
        if turn_on then pcall(h.set_string_property, h, BTFD, "BTenable", "1:1") end
    elseif ok3 then
        local ok4, name = pcall(h.get_string_property, h, BTFD, "BTconnectedDevName")
        if ok4 then status = (name and name ~= "") and name or "none" end
    end
    pcall(h.close, h)
    return status
end

-- Runs fn once headphones are connected (or when we can't tell). Otherwise tells the user how.
function Kambience:withHeadphones(fn, waited)
    local st = bt_status(not waited)
    if st == nil or (st ~= "off" and st ~= "none") then return fn() end
    waited = waited or 0
    if waited == 0 then
        UIManager:show(InfoMessage:new{ text = _("Connecting Bluetooth headphones…"), timeout = 3 })
    end
    if waited < BT_WAIT then
        return UIManager:scheduleIn(1, function()
            if self.on then self:withHeadphones(fn, waited + 1) end
        end)
    end
    self:setOn(false)
    UIManager:show(InfoMessage:new{
        text = _("No Bluetooth headphones connected.\n\nSwitch them on, or pair them in the Kindle's own Settings → Wireless & Bluetooth, then turn kambience on again."),
    })
end

---------- Reading ----------

function Kambience:setOn(on)
    if not self.scenes then return end
    self.on = on
    self.ui.doc_settings:saveSetting("kambience_on", on) -- remembered per book
    if not on then return self:stop() end
    self:withHeadphones(function()
        self.current = nil
        self:update()
    end)
end

function Kambience:onKambienceToggle()
    if self.scenes then self:setOn(not self.on) end
    return true
end

-- Scenes embedded in the EPUB, or nil. Books tagged before the rename use the old file name.
local EMBED_PATHS = { "META-INF/kambience.json", "META-INF/kimmersive.json" }

local function read_embedded(file)
    for _, inner in ipairs(EMBED_PATHS) do
        -- KOReader's libarchive binding, when this build has it.
        local ok, text = pcall(function()
            local Archiver = require("ffi/archiver")
            local r = Archiver.Reader:new()
            if not r:open(file) then return nil end
            local s = r:extractToMemory(inner)
            r:close()
            return s
        end)
        if ok and text and #text > 0 then return text end
        -- Otherwise the Kindle's own unzip.
        local p = io.popen("unzip -p '" .. file:gsub("'", "'\\''") .. "' " .. inner .. " 2>/dev/null")
        if p then
            text = p:read("*a")
            p:close()
            if text and #text > 0 then return text end
        end
    end
end

-- A separate <book>.kambience.json next to the book wins; otherwise the copy inside the EPUB.
local function read_scenes(file)
    local f = io.open(file:gsub("%.[^./]+$", "") .. ".kambience.json", "r")
    if f then
        local text = f:read("*a")
        f:close()
        return text
    end
    return read_embedded(file)
end

function Kambience:onReaderReady()
    self.scenes, self.on = nil, false
    if not self.ui.rolling then return end -- EPUB-style documents only
    local path = self.ui.document.file
    local text = read_scenes(path)
    if not text then return end -- no sounds: the plugin stays out of the way
    local ok, data = pcall(json.decode, text)
    if not (ok and type(data) == "table" and type(data.scenes) == "table") then
        return logger.warn("kambience: cannot parse", path)
    end
    self.scenes = data.scenes
    local ds = self.ui.doc_settings
    if ds:isTrue("kambience_on") then
        self:setOn(true)
    elseif not ds:isTrue("kambience_hinted") then
        ds:saveSetting("kambience_hinted", true)
        UIManager:show(InfoMessage:new{
            text = _("This book has kambience sounds.\nTurn them on in the menu: kambience."),
            timeout = 5,
        })
    end
end

function Kambience:update()
    if not (self.scenes and self.on) then return end
    local doc = self.ui.document
    local frag = tonumber((doc:getXPointer() or ""):match("^/body/DocFragment%[(%d+)%]"))
    if not frag then return end
    local start = doc:getPageFromXPointer("/body/DocFragment[" .. frag .. "]")
    local nxt = doc:getPageFromXPointer("/body/DocFragment[" .. (frag + 1) .. "]")
    if not nxt or nxt <= start then nxt = doc:getPageCount() + 1 end
    local scene = Scenes.find(self.scenes, frag, Scenes.fraction(doc:getCurrentPage(), start, nxt))
    local label = scene and scene.sound or "silence"
    if label == self.current then return end
    self.current = label
    local file
    for _, dir in ipairs(SOUND_DIRS) do
        file = Scenes.resolve(dir, label)
        if file then break end
    end
    logger.dbg("kambience: scene", label, file)
    if file then self:play(file) else self:fadeOut() end
end

function Kambience:onPageUpdate() self:update() end
function Kambience:onPosUpdate() self:update() end -- scroll mode
function Kambience:onCloseDocument() if self.on then self:stop() end end
function Kambience:onSuspend()
    -- The device is going to sleep, so there is no time to fade: stop now.
    if not self.on then return end
    self.fade_id = (self.fade_id or 0) + 1
    self.current, self.vol_now = nil, 0
    send({ "stop" })
end
function Kambience:onResume() if self.on then self:setOn(true) end end -- re-checks headphones

function Kambience:addToMainMenu(menu_items)
    local vols = {}
    for _, v in ipairs({ 20, 30, 40, 60, 80 }) do
        table.insert(vols, {
            text = v .. "%",
            checked_func = function() return self.settings.volume == v end,
            callback = function()
                self.settings.volume = v
                if (self.vol_now or 0) > 0 then self:fadeTo(v) end
            end,
        })
    end
    menu_items.kambience = {
        text = _("kambience"),
        -- Greyed out for books without a .kambience.json.
        enabled_func = function() return self.scenes ~= nil end,
        sub_item_table = {
            {
                text = _("Play sounds for this book"),
                checked_func = function() return self.on end,
                callback = function() self:setOn(not self.on) end,
            },
            { text = _("Volume"), sub_item_table = vols },
            {
                text = _("Current scene"),
                keep_menu_open = true,
                callback = function()
                    UIManager:show(InfoMessage:new{ text = self.on and (self.current or "…") or _("Sounds are off") })
                end,
            },
        },
    }
end

return Kambience
