-- ChartsCapture hook: TCP server on 127.0.0.1:47123, places the F10 camera top-down.
-- Protocol (one text line per request, one text line per reply):
--   "xz <x> <z> <alt>"      -> reply "ok <x> <z> <alt>"
--   "ll <lat> <lon> <alt>"  -> reply "ok <x> <z> <alt>"  (x North, z East, metres)
--   "release"               -> stops commanding the camera, reply "ok released"
--   "cam <x> <z> <h> <heading> <pitch>" -> free camera at ground height + h metres (ground height from LoGetAltitude(x, z)),
--                              heading in degrees (0 = +x north, 90 = +z east), pitch in degrees (negative = down);
--                              reply "ok <x> <y> <z>" (the y placed) or "err no_altitude" (nothing placed)
-- Camera is commanded only after a request, and released on "release", mission stop,
-- or automatically after 5 s without any request.

local ccSocket = require('socket')

local cc = { server = nil, clients = {}, target = nil, lastReq = 0 }
local CC_IDLE_RELEASE_S = 5 -- camera released if no request received for this long

local prevStart = LuaExportStart
local prevBefore = LuaExportBeforeNextFrame
local prevStop = LuaExportStop

local function ccLog(msg)
    log.write('ChartsCapture', log.INFO, msg)
end

local function ccHandleCam(line)
    local v = {}
    for n in line:gmatch('[%-%d%.eE]+') do v[#v + 1] = tonumber(n) end
    local x, z, h, cap, pitch = v[1], v[2], v[3], v[4], v[5]
    if not (x and z and h and cap and pitch) then return 'err bad_request' end
    local ok, ground = pcall(LoGetAltitude, x, z)
    if not ok or type(ground) ~= 'number' then return 'err no_altitude' end
    local y = ground + h
    cc.target = { cam = true, x = x, y = y, z = z, cap = cap, pitch = pitch }
    return string.format('ok %.3f %.3f %.3f', x, y, z)
end

local function ccHandle(line)
    cc.lastReq = ccSocket.gettime()
    if line:match('^cam[%s]') then return ccHandleCam(line) end
    local cmd, a, b, c = line:match('^(%a+)%s*([%-%d%.eE]*)%s*([%-%d%.eE]*)%s*([%-%d%.eE]*)')
    if cmd == 'release' then
        cc.target = nil
        return 'ok released'
    end
    a, b, c = tonumber(a), tonumber(b), tonumber(c)
    if not (a and b and c) then return 'err bad_request' end
    local x, z
    if cmd == 'xz' then
        x, z = a, b
    elseif cmd == 'll' then
        local p = LoGeoCoordinatesToLoCoordinates(b, a) -- (lon, lat)
        if not p then return 'err geo_failed' end
        x, z = p.x, p.z
    else
        return 'err unknown_command'
    end
    cc.target = { x = x, z = z, alt = c }
    return string.format('ok %.3f %.3f %.3f', x, z, c)
end

local function ccPoll()
    if not cc.server then return end
    while true do
        local cl = cc.server:accept()
        if not cl then break end
        cl:settimeout(0)
        cc.clients[#cc.clients + 1] = { sock = cl, buf = '' }
    end
    for i = #cc.clients, 1, -1 do
        local c = cc.clients[i]
        local data, err, partial = c.sock:receive('*l')
        local line = data
        if not line and partial and #partial > 0 then
            c.buf = c.buf .. partial
        end
        if line then
            line = c.buf .. line
            c.buf = ''
            local ok, reply = pcall(ccHandle, line)
            c.sock:send((ok and reply or ('err ' .. tostring(reply))) .. '\n')
        elseif err == 'closed' then
            c.sock:close()
            table.remove(cc.clients, i)
        end
    end
end

local function ccApplyCamera()
    local t = cc.target
    if not t then return end
    if ccSocket.gettime() - cc.lastReq > CC_IDLE_RELEASE_S then
        cc.target = nil
        ccLog('camera released: no request for ' .. CC_IDLE_RELEASE_S .. ' s')
        return
    end
    if t.cam then
        local hd, pt = math.rad(t.cap), math.rad(t.pitch)
        local ch, sh, cp, sp = math.cos(hd), math.sin(hd), math.cos(pt), math.sin(pt)
        LoSetCameraPosition({
            x = { x = cp * ch, y = sp, z = cp * sh },       -- forward
            y = { x = -sp * ch, y = cp, z = -sp * sh },     -- up
            z = { x = -sh, y = 0, z = ch },                 -- right
            p = { x = t.x, y = t.y, z = t.z },
        })
        return
    end
    LoSetCameraPosition({
        x = { x = 0, y = -1, z = 0 },
        y = { x = 1, y = 0, z = 0 },
        z = { x = 0, y = 0, z = 1 },
        p = { x = t.x, y = t.alt, z = t.z },
    })
end

LuaExportStart = function()
    if prevStart then pcall(prevStart) end
    pcall(function()
        if not cc.server then
            local s = assert(ccSocket.bind('127.0.0.1', 47123))
            s:settimeout(0)
            cc.server = s
            ccLog('listening on 127.0.0.1:47123')
        end
    end)
end

LuaExportBeforeNextFrame = function()
    if prevBefore then pcall(prevBefore) end
    local ok, err = pcall(function() ccPoll(); ccApplyCamera() end)
    if not ok then ccLog('error: ' .. tostring(err)) end
end

LuaExportStop = function()
    if prevStop then pcall(prevStop) end
    pcall(function()
        cc.target = nil
        for _, c in ipairs(cc.clients) do c.sock:close() end
        cc.clients = {}
        if cc.server then cc.server:close(); cc.server = nil end
        ccLog('stopped')
    end)
end
