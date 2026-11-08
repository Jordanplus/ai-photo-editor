-- 手動觸發（圖庫 > 增效工具額外功能）：立即處理 inbox。
local LrTasks = import "LrTasks"
local Bridge = require "Bridge"
LrTasks.startAsyncTask(function()
	Bridge.processInbox()
end)
