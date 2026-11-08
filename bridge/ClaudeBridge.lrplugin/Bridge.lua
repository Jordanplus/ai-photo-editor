--[[
Claude Bridge：讀取 inbox 內的指令檔（*.cmd，每行一個指令），在 Lightroom 內執行，
結果寫到 done/<同名>.log，指令檔移到 done/。

指令（以 TAB 分隔欄位）：
  collection<TAB>名稱                  之後加入的照片都放進這個收藏集
  add<TAB>/abs/path.DNG                以「新增」方式加入目錄（已存在則略過），並記錄讀到的調整值
  export<TAB>/abs/path.DNG<TAB>/out/dir<TAB>長邊像素   用 Lightroom 目前設定匯出 sRGB JPEG
  dev<TAB>/abs/path.DNG<TAB>key=value;key=value      套用調整（數值）
]]
local LrApplication = import "LrApplication"
local LrFileUtils = import "LrFileUtils"
local LrPathUtils = import "LrPathUtils"
local LrExportSession = import "LrExportSession"
local LrTasks = import "LrTasks"

-- 外掛資料夾（ClaudeBridge.lrplugin）的上一層，就是 inbox/、done/ 所在的 bridge/
local ROOT = LrPathUtils.parent(_PLUGIN.path)
local INBOX = ROOT .. "/inbox"
local DONE = ROOT .. "/done"

local Bridge = {}

local function split(line, sep)
	local out = {}
	for field in string.gmatch(line .. sep, "(.-)" .. sep) do
		out[#out + 1] = field
	end
	return out
end

local function devSummary(photo)
	local s = photo:getDevelopSettings() or {}
	local keys = { "ProcessVersion", "Exposure2012", "Highlights2012", "Shadows2012", "Dehaze", "Vibrance",
		"SplitToningHighlightSaturation", "LuminanceSmoothing", "CameraProfile" }
	local parts = {}
	for _, k in ipairs(keys) do
		parts[#parts + 1] = k .. "=" .. tostring(s[k])
	end
	return table.concat(parts, " ")
end

local function runFile(path)
	local catalog = LrApplication.activeCatalog()
	local log = {}
	local function L(msg) log[#log + 1] = msg end
	local text = LrFileUtils.readFile(path) or ""
	local collectionName = nil
	local pendingExports = {}

	for line in string.gmatch(text, "[^\r\n]+") do
		local f = split(line, "\t")
		local cmd = f[1]
		local ok, err = LrTasks.pcall(function()
			if cmd == "collection" then
				collectionName = f[2]
				L("collection " .. collectionName)
			elseif cmd == "add" then
				local photo = catalog:findPhotoByPath(f[2])
				local status = "exists"
				catalog:withWriteAccessDo("Claude Bridge add", function()
					if not photo then
						photo = catalog:addPhoto(f[2])
						status = "added"
					end
					if collectionName then
						local col = catalog:createCollection(collectionName, nil, true)
						col:addPhotos({ photo })
					end
				end, { timeout = 60 })
				L("add " .. status .. " " .. f[2] .. " | " .. devSummary(photo))
			elseif cmd == "dev" then
				local photo = catalog:findPhotoByPath(f[2])
				assert(photo, "not in catalog: " .. f[2])
				local settings = {}
				for kv in string.gmatch(f[3] or "", "[^;]+") do
					local k, v = string.match(kv, "^%s*([%w_]+)%s*=%s*(.-)%s*$")
					settings[k] = tonumber(v) or v
				end
				catalog:withWriteAccessDo("Claude Bridge develop", function()
					photo:applyDevelopSettings(settings, "Claude Bridge")
				end, { timeout = 60 })
				L("dev " .. f[2] .. " | " .. devSummary(photo))
			elseif cmd == "export" then
				local photo = catalog:findPhotoByPath(f[2])
				assert(photo, "not in catalog: " .. f[2])
				local key = f[3] .. "|" .. (f[4] or "1600")
				pendingExports[key] = pendingExports[key] or { dir = f[3], size = tonumber(f[4] or "1600"), photos = {} }
				table.insert(pendingExports[key].photos, photo)
			else
				L("unknown command: " .. tostring(cmd))
			end
		end)
		if not ok then L("ERROR " .. line .. " :: " .. tostring(err)) end
	end

	for _, job in pairs(pendingExports) do
		local ok, err = LrTasks.pcall(function()
			LrFileUtils.createAllDirectories(job.dir)
			local session = LrExportSession({
				photosToExport = job.photos,
				exportSettings = {
					LR_export_destinationType = "specificFolder",
					LR_export_destinationPathPrefix = job.dir,
					LR_export_useSubfolder = false,
					LR_collisionHandling = "overwrite",
					LR_format = "JPEG",
					LR_jpeg_quality = 0.85,
					LR_export_colorSpace = "sRGB",
					LR_size_doConstrain = true,
					LR_size_resizeType = "longEdge",
					LR_size_maxWidth = job.size,
					LR_size_maxHeight = job.size,
					LR_size_units = "pixels",
					LR_size_doNotEnlarge = true,
					LR_outputSharpeningOn = false,
					LR_reimportExportedPhoto = false,
					LR_renamingTokensOn = false,
					LR_minimizeEmbeddedMetadata = true,
					LR_removeLocationMetadata = false,
				},
			})
			session:doExportOnCurrentTask()
			for _, rendition in session:renditions() do
				local success, pathOrMsg = rendition:waitForRender()
				L("export " .. (success and "ok " or "FAIL ") .. tostring(pathOrMsg))
			end
		end)
		if not ok then L("ERROR export " .. job.dir .. " :: " .. tostring(err)) end
	end

	local base = LrPathUtils.leafName(path)
	local fh = io.open(DONE .. "/" .. base .. ".log", "w")
	if fh then
		fh:write(table.concat(log, "\n") .. "\n")
		fh:close()
	end
	LrFileUtils.move(path, DONE .. "/" .. base)
end

function Bridge.processInbox()
	for file in LrFileUtils.files(INBOX) do
		if string.sub(file, -4) == ".cmd" then
			runFile(file)
		end
	end
end

return Bridge
