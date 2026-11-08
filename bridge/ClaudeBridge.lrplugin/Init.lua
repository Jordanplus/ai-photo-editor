-- 外掛載入時啟動背景輪詢：每 2 秒看一次 inbox 有沒有新指令。
local LrTasks = import "LrTasks"
local Bridge = require "Bridge"

_G.claudeBridgeRunning = true
LrTasks.startAsyncTask(function()
	while _G.claudeBridgeRunning do
		LrTasks.pcall(Bridge.processInbox)
		LrTasks.sleep(2)
	end
end)
