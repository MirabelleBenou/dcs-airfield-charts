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

local function finir(cause, mot)
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
    for _, nom in ipairs({ 'fin.txt', 'vue_demande.txt', 'vue_faite.txt', 'battement.txt' }) do os.remove(chemin(nom)) end
    -- desarme : une nuit coupee (PC plante, courant) ne reprend jamais une session de jeu suivante ; l'armement reste en memoire
    local ok0, err0 = pcall(ecrire_texte, 'file.lua', 'return false\n')
    if not ok0 then jlog('file.lua non desarme : ' .. tostring(err0)) end
    pouls()
    local ok, r = pcall(os.execute, E.q.lancer)
    jlog('lance : ' .. E.q.lancer .. ' -> ' .. tostring(ok and r or ('erreur ' .. tostring(r))))
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
end

function cb.onSimulationStop()
    if E and E.phase ~= 'fini' then jlog('mission arretee (phase ' .. E.phase .. ')') end
    E = nil
end

local ok, err = pcall(function() Sim.setUserCallbacks(cb) end)
if not ok then jlog('Sim.setUserCallbacks : erreur ' .. tostring(err)) end
