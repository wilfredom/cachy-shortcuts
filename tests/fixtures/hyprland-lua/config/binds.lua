local mainMod = "SUPER"
local noctCall = "noctalia msg "
local launchPrefix = "uwsm app -- "

-- Digit d -> evdev keycode (AZERTY-safe): 1..9 => 10..18, 0 => 19
local function digitCode(d)
    return "code:" .. (d == 0 and 19 or (9 + d))
end

-- hl.bind("SUPER + Z", hl.dsp.exec_cmd("a commented-out bind is not a bind"))
local notes = [[
hl.bind("SUPER + Y", hl.dsp.exec_cmd("nor is one inside a long string"))
]]

---- WINDOWS ----
hl.bind(mainMod .. " + Q",           hl.dsp.window.close())
hl.bind(mainMod .. " + Return",      hl.dsp.exec_cmd(launchPrefix .. TERMINAL))
hl.bind(mainMod .. " + ALT + Space", hl.dsp.window.float({ action = "toggle" }))
hl.bind("ALT + Tab",                 hl.dsp.window.cycle_next())
hl.bind(mainMod .. " + Left",        hl.dsp.focus({ direction = "left" }))

---- LAUNCHER ----
hl.bind(mainMod .. " + W",     hl.dsp.exec_cmd(launchPrefix .. BROWSER), { description = "Browser" })
hl.bind(mainMod .. " + Space", hl.dsp.exec_cmd(noctCall .. "panel-toggle launcher"))

---- HARDWARE ----
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd(noctCall .. "volume-up"), { locked = true, repeating = true })

---- MOUSE ----
hl.bind(mainMod .. " + mouse:272",  hl.dsp.window.drag())
hl.bind(mainMod .. " + mouse_down", hl.dsp.focus({ workspace = "m-1" }))

---- INLINE FUNCTIONS ----
local function zoom(step) end
hl.bind(mainMod .. " + Minus",   function() zoom(-0.3) end, { repeating = true })
hl.bind(mainMod .. " + code:82", function() zoom(-0.3) end, { repeating = true })

---- GENERATED: a helper, and loops ----
hl.bind(mainMod .. " + SHIFT + " .. digitCode(1), hl.dsp.window.move({ monitor = MONITOR1 }))
for i = 1, NUM_WPM do
    local key = i % 10
    hl.bind(mainMod .. " + ALT + " .. digitCode(key), hl.dsp.focus({ workspace = i }))
end

---- SUBMAP ----
hl.define_submap("resize", "reset", function()
    hl.bind("H",      hl.dsp.window.resize({ x = -10, y = 0, relative = true }))
    hl.bind("Escape", hl.dsp.submap("reset"))
end)
hl.bind(mainMod .. " + R", hl.dsp.submap("resize"))

---- REMOVED ----
hl.bind(mainMod .. " + B", hl.dsp.exec_cmd("never-runs"))
hl.unbind(mainMod .. " + B")

---- LAST ----
hl.bind(mainMod .. " + S", hl.dsp.workspace.toggle_special())
