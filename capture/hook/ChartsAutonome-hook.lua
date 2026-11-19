local DOSSIER = nil -- chemin de <work_dir>\autonome\ , par exemple [[D:\Captures\autonome\]] ; nil : ce crochet ne fait rien du tout
-- ChartsAutonome-hook.lua : crochet du GUI de DCS (Saved Games\<DCS>\Scripts\Hooks\) pour les captures de nuit sans personne au PC.
-- Il lit <DOSSIER>file.lua a chaque onSimulationStart. Sans DOSSIER, ou si file.lua est absent, illisible, ne rend pas une table
-- (par exemple `return false`), il ne fait rien (ni journal, ni pouls). Une table d'armement :
--   { arme_le = os.time(), budget_s = <n>, attente_s = <n>, eteindre_au_budget = <bool>, lancer = [[...]] }
-- `lancer` : la commande qui demarre nuit.py, avec des chemins absolus, le titre vide de `start` et /min (la console ne passe pas
-- devant DCS, que f10capture capture par le bureau). Elle doit commencer par `start` (os.execute rend alors la main tout de suite) :
--   start "" /min "<python.exe>" -I "<depot>\capture\autonome\nuit.py" jouer "<work_dir>\autonome\nuit.json"
-- `budget_s` doit couvrir budget_captures_s + delai_commande_s + le rassemblement et le depot (voir la derniere ligne de
-- `nuit.py verifier <nuit.json>`) : au-dela, le crochet ferme DCS, meme si nuit.py n'a pas fini.
-- Un armement dont arme_le a plus de 12 heures, ou qui est dans le futur (plus de 5 minutes), est ignore.
-- Quand la mission vole (Sim.getPause() faux) et apres attente_s, il remet file.lua a `return false` (la nuit ne reprendra pas une
-- session de jeu suivante), puis lance `lancer` une seule fois (os.execute), puis a chaque seconde : pouls dans battement.txt
-- toutes les 30 s, vue F10/F11 a la demande (vue_demande.txt -> vue_faite.txt), fin.txt, budget. Un fichier vide est un fichier
-- que l'autre cote est en train d'ecrire : il n'est ni lu ni supprime, il est relu a la seconde suivante. A la fin il remet file.lua
-- a `return false`, eteint le PC si on le lui a dit (shutdown /s /t 120) puis ferme DCS. Journal : crochet.log. Seuls `lancer` et
-- `shutdown` passent par os.execute (il bloque le rendu) ; chaque appel a DCS est protege (pcall) : une erreur s'ecrit au
-- journal, elle n'arrete pas DCS.
-- Releve du relief (etape `relief` de la file) : a la seconde, si releve_demande.lua est la (ecrit par nuit.py sous un nom
-- provisoire puis renomme), le crochet le lit, le supprime et lit l'altitude du sol sur une grille carree, a chaque image, jusqu'a
-- depenser budget_image_ms (Sim.getRealTime) : `export` par l'altitude d'export (Export.LoGetAltitude, sinon la globale
-- LoGetAltitude ; aucune des deux : `erreur export_absent`) ; `mission` par lots de points, un bout de Lua
-- execute dans l'environnement de la mission (net.dostring_in -> a_do_script : land.getHeight, et land.getSurfaceType si
-- `surface`). Le centre vient de Export.LoGeoCoordinatesToLoCoordinates, sinon de la globale, sinon de coord.LLtoLO par la
-- passerelle (cle `geo` du .json : export, globale ou mission). Sortie : le CSV de `sortie` et, a cote, un .json avec le debit ; puis releve_fait.txt = `ok`, `annule` ou
-- `erreur <raison>`. releve_annule.txt arrete le releve en cours. Un seul releve a la fois.

if DOSSIER == nil then return end

local Sim = Sim or DCS

local COMMANDE_F10 = 15  -- iCommandViewAWACS
local COMMANDE_F11 = 16  -- iCommandViewFree
local ARMEMENT_MAX_S = 12 * 3600
local ARMEMENT_FUTUR_S = 300
local POULS_S = 30

local cb = {}
local E = nil            -- la nuit en cours (nil : rien)
local deja_lance = false -- `lancer` ne part qu'une fois par processus DCS

local RELIEF_LOT_DEBUT = 64
local RELIEF_LOT_MAX = 16384
local RELIEF_POINTS_MAX = 4000000
local RELIEF_ENTETE = 'i,j,x,z,alt_m,surface\n'

if not DOSSIER:match('[\\/]$') then
    DOSSIER = DOSSIER .. ((DOSSIER:find('/', 1, true) and not DOSSIER:find('\\', 1, true)) and '/' or '\\')
end

local function chemin(nom) return DOSSIER .. nom end

local function maintenant()
    local ok, t = pcall(function() return Sim.getRealTime() end)
    if ok and type(t) == 'number' then return t end
    return nil
end

local function jlog(msg)
    pcall(function()
        local f = io.open(chemin('crochet.log'), 'a')
        if f then
            f:write(os.date('%Y-%m-%d %H:%M:%S') .. string.format(' [%.1f] ', maintenant() or 0) .. msg .. '\n')
            f:close()
        end
    end)
end

local function lire_texte(nom)
    local f = io.open(chemin(nom), 'r')
    if not f then return nil end
    local s = f:read('*a')
    f:close()
    return s or ''
end

local function ecrire_texte(nom, s)
    local f = io.open(chemin(nom), 'w')
    if not f then return false end
    f:write(s)
    f:close()
    return true
end

local function pouls()
    pcall(ecrire_texte, 'battement.txt', tostring(os.time()))
end

local function demarrer()
    E = nil
    local f = io.open(chemin('file.lua'), 'r')
    if not f then return end
    f:close()
    local ok, q = pcall(dofile, chemin('file.lua'))
    if not ok or type(q) ~= 'table' then return end
    if deja_lance then jlog('armement ignore : la file est deja partie dans cette session'); return end
    if type(q.arme_le) ~= 'number' or os.time() - q.arme_le > ARMEMENT_MAX_S then
        jlog('armement ignore : arme_le absent ou de plus de 12 heures')
        return
    end
    if q.arme_le > os.time() + ARMEMENT_FUTUR_S then
        jlog('armement ignore : arme_le dans le futur (millisecondes ?)')
        return
    end
    if type(q.budget_s) ~= 'number' or type(q.lancer) ~= 'string' or not q.lancer:match('^%s*start%s') then
        jlog('armement ignore : budget_s ou lancer manquant, ou lancer ne commence pas par start')
        return
    end
    E = { q = q, phase = 'attente', t_libre = nil, t_tick = nil, t0 = nil, t_pouls = nil,
          attente_s = (type(q.attente_s) == 'number') and q.attente_s or 10 }
    jlog('arme : budget ' .. tostring(q.budget_s) .. ' s, attente ' .. tostring(E.attente_s) .. ' s')
end

-- ------------------------------------------------------------------ le releve du relief

-- ce que l'environnement du crochet offre (des globales seulement) : une ligne au journal, une fois par nuit
local function journal_environnement()
    local function genre(nom, champ)
        local ok, v = pcall(function()
            local o = _G[nom]
            if champ == nil then return type(o) end
            if o == nil then return 'nil' end
            return type(o[champ])
        end)
        return ok and v or 'erreur'
    end
    jlog('environnement du crochet : Export.LoGetAltitude=' .. genre('Export', 'LoGetAltitude')
        .. ', LoGetAltitude=' .. genre('LoGetAltitude')
        .. ', Export.LoGeoCoordinatesToLoCoordinates=' .. genre('Export', 'LoGeoCoordinatesToLoCoordinates')
        .. ', LoGeoCoordinatesToLoCoordinates=' .. genre('LoGeoCoordinatesToLoCoordinates')
        .. ', net=' .. genre('net') .. ', net.dostring_in=' .. genre('net', 'dostring_in')
        .. ', Terrain=' .. genre('Terrain') .. ', Terrain.GetHeight=' .. genre('Terrain', 'GetHeight')
        .. ', Terrain.GetSurfaceType=' .. genre('Terrain', 'GetSurfaceType'))
end

-- la reponse d'un releve : ecrite sous un nom provisoire puis renommee (nuit.py ne lit jamais un fichier a moitie ecrit)
local function repondre_releve(mot)
    local ok = pcall(function()
        local f = assert(io.open(chemin('releve_fait.tmp'), 'w'))
        f:write(mot)
        f:close()
        os.remove(chemin('releve_fait.txt'))
        assert(os.rename(chemin('releve_fait.tmp'), chemin('releve_fait.txt')))
    end)
    if not ok then pcall(ecrire_texte, 'releve_fait.txt', mot) end
end

-- une ecriture du releve n'est un echec que si elle le dit : erreur levee, ou nil accompagne d'un message (convention de Lua).
-- DCS ne rend rien d'un file:write reussi : un retour vide, nil seul ou false seul n'est pas un echec.
local function ecriture_en_echec(fh, texte)
    local ok, r1, r2 = pcall(function() return fh:write(texte) end)
    if not ok then return true end
    return r1 == nil and r2 ~= nil
end

local function json_chaine(s)
    return '"' .. (tostring(s):gsub('[%c"\\]', function(c) return string.format('\\u%04x', c:byte()) end)) .. '"'
end

-- le releve en cours est fini : CSV ferme, .json ecrit a cote, puis la reponse (fin : complet, annule, nuit, erreur)
local function terminer_releve(fin, raison)
    local R = E.releve
    if not R then return end
    E.releve = nil
    pcall(function()
        if #R.buf > 0 then R.fh:write(table.concat(R.buf)) end
    end)
    pcall(function() R.fh:close() end)
    local duree = (R.t_premier and R.t_dernier) and (R.t_dernier - R.t_premier) or 0
    local ok, err = pcall(function()
        local f = assert(io.open(R.json, 'wb'))
        f:write('{"id":' .. json_chaine(R.id)
            .. ',"lat":' .. string.format('%.12g', R.lat) .. ',"lon":' .. string.format('%.12g', R.lon)
            .. ',"x0":' .. string.format('%.12g', R.x0) .. ',"z0":' .. string.format('%.12g', R.z0)
            .. ',"geo":' .. json_chaine(R.geo)
            .. ',"n":' .. string.format('%d', R.n) .. ',"pas_m":' .. string.format('%.12g', R.pas_m)
            .. ',"methode":' .. json_chaine(R.methode) .. ',"surface":' .. tostring(R.surface)
            .. ',"budget_image_ms":' .. string.format('%.12g', R.budget_image_ms)
            .. ',"points":' .. string.format('%d', R.points) .. ',"erreurs":' .. string.format('%d', R.erreurs)
            .. ',"duree_s":' .. string.format('%.3f', duree) .. ',"images":' .. string.format('%d', R.images)
            .. ',"points_par_s":' .. (duree > 0 and string.format('%.2f', R.points / duree) or 'null')
            .. ',"lot_max":' .. (R.lot_max and string.format('%d', R.lot_max) or 'null')
            .. ',"fin":' .. json_chaine(fin) .. '}\n')
        f:close()
    end)
    if not ok then jlog('releve ' .. R.id .. ' : .json non ecrit : ' .. tostring(err)) end
    if fin == 'annule' then pcall(os.remove, chemin('releve_annule.txt')) end
    jlog('releve ' .. R.id .. ' : ' .. fin .. (raison and (' (' .. raison .. ')') or '') .. ', ' .. R.points .. ' point(s), '
        .. R.erreurs .. ' erreur(s)')
    if fin == 'complet' then
        repondre_releve('ok')
    elseif fin == 'erreur' then
        repondre_releve('erreur ' .. tostring(raison))
    else
        repondre_releve('annule')
    end
end

local function nombre_fini(v) return type(v) == 'number' and v == v and v > -math.huge and v < math.huge end

-- controle de la table releve_demande.lua : (true) ou (nil, raison). surface et budget_image_ms absents : leurs defauts.
local function controler_demande(q)
    if type(q) ~= 'table' then return nil, 'demande illisible' end
    if q.id ~= nil and (type(q.id) ~= 'string' or q.id == '') then return nil, 'champ id' end
    if not nombre_fini(q.lat) or q.lat < -90 or q.lat > 90 then return nil, 'champ lat' end
    if not nombre_fini(q.lon) or q.lon < -180 or q.lon > 180 then return nil, 'champ lon' end
    if not nombre_fini(q.n) or q.n ~= math.floor(q.n) or q.n < 2 or q.n * q.n > RELIEF_POINTS_MAX then return nil, 'champ n' end
    if not nombre_fini(q.pas_m) or q.pas_m <= 0 then return nil, 'champ pas_m' end
    if q.methode ~= 'export' and q.methode ~= 'mission' then return nil, 'champ methode' end
    if q.surface ~= nil and type(q.surface) ~= 'boolean' then return nil, 'champ surface' end
    if q.surface and q.methode == 'export' then return nil, 'champ surface avec export' end
    if q.budget_image_ms ~= nil and (not nombre_fini(q.budget_image_ms) or q.budget_image_ms <= 0 or q.budget_image_ms > 1000) then
        return nil, 'champ budget_image_ms'
    end
    if type(q.sortie) ~= 'string' or q.sortie == '' then return nil, 'champ sortie' end
    return true
end

local ENVELOPPE_DEBUT = "return (function(a, b) if type(a) == 'string' and a ~= '' then return a end if type(b) == 'string' then return b end return '' end)(a_do_script([====["
local ENVELOPPE_FIN = "]====]))"

-- la passerelle : un bout de Lua execute dans l'environnement de la mission ; rend sa chaine, ou nil
local function passerelle(code)
    local ok, rep = pcall(function() return net.dostring_in('mission', ENVELOPPE_DEBUT .. code .. ENVELOPPE_FIN) end)
    if ok and type(rep) == 'string' then return rep end
    return nil
end

-- l'altitude d'export : Export.LoGetAltitude si c'est une fonction, sinon la globale LoGetAltitude ; nil si aucune des deux
local function choisir_altitude()
    local ok1, f1 = pcall(function() return type(Export) == 'table' and type(Export.LoGetAltitude) == 'function' and Export.LoGetAltitude end)
    if ok1 and f1 then return f1, 'export' end
    local ok2, f2 = pcall(function() return type(LoGetAltitude) == 'function' and LoGetAltitude end)
    if ok2 and f2 then return f2, 'globale' end
    return nil
end

local CHUNK_GEO = [==[
local ok, p = pcall(function() return coord.LLtoLO(LAT, LON) end)
if not ok or type(p) ~= 'table' or type(p.x) ~= 'number' or type(p.z) ~= 'number' then return 'ERR', 0 end
return string.format('OK\n%.17g %.17g', p.x, p.z), 0
]==]

-- le centre en coordonnees de DCS : (x, z, chemin) ou nil. Export.LoGeoCoordinatesToLoCoordinates, sinon la globale, sinon
-- coord.LLtoLO par la passerelle (x nord, z est) ; chacun est essaye a son tour s'il echoue.
local function centre_geographique(lon, lat)
    local candidats = {}
    local ok1, f1 = pcall(function() return type(Export) == 'table' and type(Export.LoGeoCoordinatesToLoCoordinates) == 'function' and Export.LoGeoCoordinatesToLoCoordinates end)
    if ok1 and f1 then candidats[#candidats + 1] = { 'export', f1 } end
    local ok2, f2 = pcall(function() return type(LoGeoCoordinatesToLoCoordinates) == 'function' and LoGeoCoordinatesToLoCoordinates end)
    if ok2 and f2 then candidats[#candidats + 1] = { 'globale', f2 } end
    for _, c in ipairs(candidats) do
        local ok, p = pcall(c[2], lon, lat)
        if ok and type(p) == 'table' and nombre_fini(p.x) and nombre_fini(p.z) then return p.x, p.z, c[1] end
    end
    local rep = passerelle(string.format('local LAT, LON = %.17g, %.17g\n', lat, lon) .. CHUNK_GEO)
    if rep then
        local sx, sz = rep:match('^OK\n(%S+) (%S+)$')
        local x, z = tonumber(sx), tonumber(sz)
        if nombre_fini(x) and nombre_fini(z) then return x, z, 'mission' end
    end
    return nil
end

local function servir_releve()
    local brut = lire_texte('releve_demande.lua')
    if not brut or not brut:find('%S') then return end    -- absent ; ou vide : l'autre cote est en train de l'ecrire
    local ok, q = pcall(dofile, chemin('releve_demande.lua'))
    os.remove(chemin('releve_demande.lua'))
    if E.releve then
        jlog('releve demande alors qu\'un releve est en cours')
        repondre_releve('erreur deja en cours')
        return
    end
    local valide, raison = controler_demande(ok and q or nil)
    if not valide then
        jlog('releve refuse : ' .. tostring(ok and raison or ('demande illisible : ' .. tostring(q))))
        repondre_releve('erreur ' .. (ok and raison or 'demande illisible'))
        return
    end
    local id = q.id or 'releve'
    local lire_alt = nil
    if q.methode == 'export' then
        lire_alt = choisir_altitude()                -- une fois, au debut du releve
        if not lire_alt then
            jlog('releve ' .. id .. ' : ni Export.LoGetAltitude ni LoGetAltitude')
            repondre_releve('erreur export_absent')
            return
        end
    end
    local x0, z0, geo = centre_geographique(q.lon, q.lat)
    if not x0 then
        jlog('releve ' .. id .. ' : conversion geographique impossible')
        repondre_releve('erreur geo')
        return
    end
    local fh = io.open(q.sortie, 'wb')
    if not fh or ecriture_en_echec(fh, RELIEF_ENTETE) then
        if fh then pcall(function() fh:close() end) end
        jlog('releve ' .. id .. ' : sortie non ouverte : ' .. q.sortie)
        repondre_releve('erreur sortie')
        return
    end
    local base = q.sortie:gsub('%.[Cc][Ss][Vv]$', '')
    E.releve = { id = id, lat = q.lat, lon = q.lon, x0 = x0, z0 = z0, geo = geo, lire_alt = lire_alt, n = q.n, pas_m = q.pas_m, methode = q.methode,
                 surface = q.surface == true, budget_image_ms = q.budget_image_ms or 40, fh = fh, json = base .. '.json',
                 total = q.n * q.n, k = 0, points = 0, erreurs = 0, images = 0, buf = {}, lot = RELIEF_LOT_DEBUT, lot_max = nil,
                 t_premier = nil, t_dernier = nil }
    jlog('releve ' .. id .. ' : ' .. q.n .. ' x ' .. q.n .. ' points, pas ' .. q.pas_m .. ' m, methode ' .. q.methode
        .. (E.releve.surface and ', surface' or ''))
end

-- le point k (0 a n*n-1) : i = rangee (vers le nord), j = colonne (vers l'est)
local function point_xz(R, k)
    local i = math.floor(k / R.n)
    local j = k - i * R.n
    return i, j, R.x0 + (i - (R.n - 1) / 2) * R.pas_m, R.z0 + (j - (R.n - 1) / 2) * R.pas_m
end

local function lire_point_export(R)
    local i, j, x, z = point_xz(R, R.k)
    local ok, h = pcall(R.lire_alt, x, z)
    local alt = ''
    if ok and nombre_fini(h) then
        alt = string.format('%.2f', h)
    else
        R.erreurs = R.erreurs + 1
    end
    R.buf[#R.buf + 1] = string.format('%d,%d,%.1f,%.1f,%s,\n', i, j, x, z, alt)
    R.k = R.k + 1
    R.points = R.points + 1
end

-- le bout de Lua execute dans l'environnement de la mission : il calcule lui-meme ses points, rend `OK\n` puis une ligne
-- `<alt>;<surface>` par point (vide si la lecture echoue). a_do_script rend ses valeurs decalees d'un cran et perd la derniere :
-- le bout finit par `return <chaine>, 0`, et l'enveloppe prend la chaine ou elle est.
local CHUNK_CORPS = [==[
local sortie = { 'OK\n' }
for k = K0, K1 do
  local i = math.floor(k / N)
  local j = k - i * N
  local x = X0 + (i - (N - 1) / 2) * PAS
  local z = Z0 + (j - (N - 1) / 2) * PAS
  local alt, surf = '', ''
  local ok, h = pcall(land.getHeight, { x = x, y = z })
  if ok and type(h) == 'number' and h == h and h > -1e9 and h < 1e9 then alt = string.format('%.2f', h) end
  if SURF then
    local ok2, s = pcall(land.getSurfaceType, { x = x, y = z })
    if ok2 and type(s) == 'number' and s == s then surf = string.format('%d', math.floor(s)) end
  end
  sortie[#sortie + 1] = alt .. ';' .. surf .. '\n'
end
return table.concat(sortie), 0
]==]

-- un lot de points par la passerelle ; rend nil, ou la raison de l'erreur
local function lire_lot_mission(R)
    local nb = math.min(R.lot, R.total - R.k)
    local k0, k1 = R.k, R.k + nb - 1
    local code = string.format('local X0, Z0, PAS, N, K0, K1 = %.17g, %.17g, %.17g, %.17g, %.17g, %.17g\nlocal SURF = %s\n',
        R.x0, R.z0, R.pas_m, R.n, k0, k1, R.surface and 'true' or 'false') .. CHUNK_CORPS
    R.lot_max = math.max(R.lot_max or 0, R.lot)
    local t0 = maintenant()
    local rep = passerelle(code)
    local t1 = maintenant()
    if type(rep) ~= 'string' or rep:sub(1, 3) ~= 'OK\n' then return 'passerelle' end
    local corps = rep:sub(4)
    local lus, taille, lignes = 0, 0, {}
    for alt, surf in corps:gmatch('([^;\n]*);([^\n]*)\n') do
        if alt ~= '' and not alt:find('^%-?%d+%.%d%d$') then return 'passerelle' end
        if surf ~= '' and not (R.surface and surf:find('^%-?%d+$')) then return 'passerelle' end
        lus = lus + 1
        taille = taille + #alt + #surf + 2
        local i, j, x, z = point_xz(R, k0 + lus - 1)
        if alt == '' or (R.surface and surf == '') then R.erreurs = R.erreurs + 1 end
        lignes[lus] = string.format('%d,%d,%.1f,%.1f,%s,%s\n', i, j, x, z, alt, surf)
    end
    if lus ~= nb or taille ~= #corps then return 'passerelle' end
    R.buf[#R.buf + 1] = table.concat(lignes)
    R.k = k1 + 1
    R.points = R.points + nb
    if t0 and t1 then                       -- la taille du lot suit le temps de l'appel
        local ms = (t1 - t0) * 1000
        if ms < R.budget_image_ms / 4 then
            R.lot = math.min(RELIEF_LOT_MAX, R.lot * 2)
        elseif ms > R.budget_image_ms then
            R.lot = math.max(1, math.floor(R.lot / 2))
        end
    end
    return nil
end

-- a chaque image : des points jusqu'a depenser budget_image_ms (au moins un point ou un lot)
local function avancer_releve()
    local R = E.releve
    if not R then return end
    local fa = io.open(chemin('releve_annule.txt'), 'r')
    if fa then
        fa:close()
        terminer_releve('annule')
        return
    end
    local t0 = maintenant()
    R.t_premier = R.t_premier or t0
    local budget_s = R.budget_image_ms / 1000
    repeat
        if R.methode == 'export' then
            lire_point_export(R)
        else
            local raison = lire_lot_mission(R)
            if raison then
                terminer_releve('erreur', raison)
                return
            end
        end
        local t = maintenant()
        if t then R.t_dernier = t end
    until R.k >= R.total or not t or not t0 or t - t0 >= budget_s
    R.images = R.images + 1
    local echec = ecriture_en_echec(R.fh, table.concat(R.buf))
    R.buf = {}
    if echec then
        terminer_releve('erreur', 'sortie')
        return
    end
    if R.k >= R.total then terminer_releve('complet') end
end

local function finir(cause, mot)
    if E.releve then pcall(terminer_releve, 'nuit') end
    E.phase = 'fini'
    local q = E.q
    jlog('fin : ' .. cause .. (mot and (' (' .. mot .. ')') or ''))
    local ok, err = pcall(ecrire_texte, 'file.lua', 'return false\n')
    if not ok then jlog('file.lua non reecrit : ' .. tostring(err)) end
    if mot == 'eteindre' or (cause == 'budget' and q.eteindre_au_budget == true) then
        local ok2, err2 = pcall(os.execute, 'shutdown /s /t 120')
        jlog('shutdown /s /t 120 : ' .. tostring(ok2 and 'lance' or ('erreur ' .. tostring(err2))))
    end
    local ok3, err3 = pcall(function() Sim.exitProcess() end)
    if not ok3 then jlog('Sim.exitProcess : erreur ' .. tostring(err3)) end
end

local function servir_vue()
    local brut = lire_texte('vue_demande.txt')
    if not brut then return end
    local mot = brut:gsub('%s+', '')
    if mot == '' then return end          -- vide : l'autre cote est en train de l'ecrire, relu a la seconde suivante
    os.remove(chemin('vue_demande.txt'))
    local commande = (mot == 'f10' and COMMANDE_F10) or (mot == 'f11' and COMMANDE_F11) or nil
    if not commande then
        ecrire_texte('vue_faite.txt', 'erreur')
        jlog('vue demandee inconnue : "' .. mot .. '"')
        return
    end
    local ok, err = pcall(function() Export.LoSetCommand(commande) end)
    if ok then
        ecrire_texte('vue_faite.txt', mot)
        jlog('vue ' .. mot .. ' posee')
    else
        ecrire_texte('vue_faite.txt', 'erreur')
        jlog('vue ' .. mot .. ' : erreur ' .. tostring(err))
    end
end

local function lancer_la_file(t)
    deja_lance = true
    E.phase, E.t0, E.t_pouls = 'marche', t, t
    for _, nom in ipairs({ 'fin.txt', 'vue_demande.txt', 'vue_faite.txt', 'battement.txt', 'releve_demande.lua', 'releve_fait.txt',
                           'releve_annule.txt' }) do os.remove(chemin(nom)) end
    -- desarme : une nuit coupee (PC plante, courant) ne reprend jamais une session de jeu suivante ; l'armement reste en memoire
    local ok0, err0 = pcall(ecrire_texte, 'file.lua', 'return false\n')
    if not ok0 then jlog('file.lua non desarme : ' .. tostring(err0)) end
    pouls()
    local ok, r = pcall(os.execute, E.q.lancer)
    jlog('lance : ' .. E.q.lancer .. ' -> ' .. tostring(ok and r or ('erreur ' .. tostring(r))))
    pcall(journal_environnement)
end

local function pas()
    local t = maintenant()
    if not t then return end
    if E.t_tick and t - E.t_tick < 1 then return end
    E.t_tick = t
    if E.phase == 'attente' then
        local ok, p = pcall(function() return Sim.getPause() end)
        if not ok then jlog('Sim.getPause : erreur ' .. tostring(p)); return end
        if p then E.t_libre = nil; return end
        E.t_libre = E.t_libre or t
        if t - E.t_libre >= E.attente_s then lancer_la_file(t) end
        return
    end
    if E.phase ~= 'marche' then return end
    if t - E.t_pouls >= POULS_S then E.t_pouls = t; pouls() end
    servir_vue()
    servir_releve()
    local mot = lire_texte('fin.txt')
    mot = mot and mot:gsub('%s+', '')
    if mot and mot ~= '' then finir('fin.txt', mot); return end
    if t - E.t0 > E.q.budget_s then finir('budget'); return end
end

function cb.onSimulationStart()
    local ok, err = pcall(demarrer)
    if not ok then E = nil; jlog('onSimulationStart : erreur ' .. tostring(err)) end
end

function cb.onSimulationFrame()
    if not E or E.phase == 'fini' then return end
    local ok, err = pcall(pas)
    if not ok then jlog('onSimulationFrame : erreur ' .. tostring(err)) end
    if E and E.phase == 'marche' and E.releve then
        local ok2, err2 = pcall(avancer_releve)
        if not ok2 then
            jlog('releve : erreur ' .. tostring(err2))
            pcall(terminer_releve, 'erreur', 'interne')
        end
    end
end

function cb.onSimulationStop()
    if E and E.phase ~= 'fini' then jlog('mission arretee (phase ' .. E.phase .. ')') end
    if E and E.releve then pcall(terminer_releve, 'nuit') end
    E = nil
end

local ok, err = pcall(function() Sim.setUserCallbacks(cb) end)
if not ok then jlog('Sim.setUserCallbacks : erreur ' .. tostring(err)) end
