// 滑动窗口功能模块 - 重构版（使用绝对日期）
(function() {
    // 滑动窗口配置
    const CONFIG = {
        windowSize: 5,           // 窗口大小（显示的日期数量）
        preloadDays: 3,          // 预加载的天数
        colWidth: 235            // 每列宽度
    };

    // 状态变量
    let state = {
        availableDates: [],      // 所有可用的日期（按时间排序）
        currentStartDateIndex: 0,  // 当前窗口起始位置的索引
        currentEndDateIndex: 0,    // 当前窗口结束位置的索引
        cachedData: {},            // 缓存的数据 {date: {records: [], ...}}
        topicOrder: [],            // 全局题材顺序（从localStorage加载）
        isInitialized: false       // 是否已初始化
    };

    // 获取服务器时间（不依赖本地时间）
    async function getServerToday() {
        try {
            const response = await fetch('/api/today');
            const data = await response.json();
            if (data.error) {
                console.error('获取服务器时间失败:', data.error);
                return null;
            }
            return data.date;
        } catch (error) {
            console.error('获取服务器时间失败:', error);
            return null;
        }
    }

    // 检查并初始化今天的数据
    async function initTodayIfNeeded() {
        try {
            const response = await fetch('/api/init-today-if-needed');
            const data = await response.json();
            return data.initialized || !data.needs_init;
        } catch (error) {
            console.error('检查今天数据失败:', error);
            return false;
        }
    }

    // 获取所有可用的日期
    async function fetchAvailableDates() {
        try {
            // 不限制 limit，获取所有有数据的交易日
            const response = await fetch('/api/recent-days?limit=1000');
            const data = await response.json();

            if (data.dates && data.dates.length > 0) {
                state.availableDates = data.dates.sort();
                return data.dates;
            }
            return [];
        } catch (error) {
            console.error('获取可用日期失败:', error);
            return [];
        }
    }

    // 获取指定日期的数据
    async function fetchDateRecords(date) {
        try {
            const response = await fetch(`/api/rotation-records-by-date?date=${date}`);
            const data = await response.json();

            if (data && !data.error) {
                return data;
            } else {
                return null;
            }
        } catch (error) {
            console.error(`获取日期 ${date} 数据失败:`, error);
            return null;
        }
    }

    // 缓存指定日期的数据
    function cacheData(date, data) {
        state.cachedData[date] = data;
    }

    // 从缓存获取数据
    function getCachedData(date) {
        return state.cachedData[date];
    }

    // 检查数据是否已缓存
    function isDataCached(date) {
        return date in state.cachedData;
    }

    // 初始化滑动窗口
    async function initialize() {
        if (state.isInitialized) {
            return;
        }


        // 加载话题顺序
        state.topicOrder = loadTopicOrder() || [];

        // 获取所有可用日期
        const dates = await fetchAvailableDates();

        if (dates.length === 0) {
            console.warn('没有可用的日期数据');
            state.isInitialized = true;
            return;
        }

        // 设置当前窗口为最后5个交易日（最新的）
        const count = Math.min(CONFIG.windowSize, dates.length);
        state.currentEndDateIndex = dates.length - 1;
        state.currentStartDateIndex = state.currentEndDateIndex - count + 1;

        if (state.currentStartDateIndex < 0) {
            state.currentStartDateIndex = 0;
        }


        // 预加载窗口内的数据
        await preloadWindowData();

        // 如果还没有话题顺序，从窗口内的题材生成初始顺序
        if (state.topicOrder.length === 0) {
            const windowTopics = getTopicsInWindow();
            const activeTopics = filterInactiveTopics(windowTopics);
            state.topicOrder = activeTopics;
            saveTopicOrder(state.topicOrder);
        }

        state.isInitialized = true;
    }

    // 预加载窗口内的数据
    async function preloadWindowData() {
        const startIdx = state.currentStartDateIndex;
        const endIdx = state.currentEndDateIndex;


        for (let i = startIdx; i <= endIdx; i++) {
            if (i < 0 || i >= state.availableDates.length) {
                continue;
            }

            const date = state.availableDates[i];

            const data = await fetchDateRecords(date);
            if (data) {
                cacheData(date, data);
            } else {
                console.warn(`日期 ${date} 的数据为空`);
            }
        }
    }

    // 获取当前窗口内的日期
    function getCurrentWindowDates() {
        const startIdx = state.currentStartDateIndex;
        const endIdx = state.currentEndDateIndex;

        return state.availableDates.slice(startIdx, endIdx + 1);
    }

    // 窗口向左移动（查看更早的日期）
    async function moveWindowLeft() {

        // 获取当前最早的日期
        const earliestIdx = state.currentStartDateIndex;

        if (earliestIdx <= 0) {

            // 获取当前最早的日期
            const earliestDate = state.availableDates[earliestIdx];

            // 查找上一个交易日（使用后端API）
            const prevResponse = await fetch(`/api/prev-trading-day?date=${earliestDate}`);
            const prevData = await prevResponse.json();


            if (prevData.error || !prevData.date) {
                return false;
            }

            const prevDate = prevData.date;

            // 如果该日期不在 availableDates 中，添加进去
            if (!state.availableDates.includes(prevDate)) {
                state.availableDates.unshift(prevDate);
                // 不需要修改索引，因为unshift后所有索引都+1了
            } else {
            }
        }

        // 移动窗口
        if (state.currentStartDateIndex > 0) {
            state.currentStartDateIndex--;
            state.currentEndDateIndex--;


            await preloadWindowData();
            return true;
        } else {
            return false;
        }
    }

    // 窗口向右移动（查看更晚的日期）
    async function moveWindowRight() {
        if (state.currentEndDateIndex >= state.availableDates.length - 1) {
            return false;
        }

        state.currentStartDateIndex++;
        state.currentEndDateIndex++;


        await preloadWindowData();
        return true;
    }

    // 滚动到指定日期
    async function scrollTo(date) {
        const idx = state.availableDates.indexOf(date);

        if (idx === -1) {
            console.warn(`未找到日期 ${date}`);
            return false;
        }

        state.currentStartDateIndex = idx;
        const endIdx = Math.min(idx + CONFIG.windowSize - 1, state.availableDates.length - 1);
        currentEndDateIndex = endIdx - (CONFIG.windowSize - 1);

        if (state.currentStartDateIndex < 0) {
            state.currentStartDateIndex = 0;
        }


        await preloadWindowData();
        return true;
    }

    // 获取当前窗口的数据（按选题分组，使用缓存的顺序）
    function getCurrentWindowData() {
        const windowDates = getCurrentWindowDates();

        // 获取窗口的起止索引
        const startIdx = state.currentStartDateIndex;
        const endIdx = state.currentEndDateIndex;


        const dateRecords = {};
        const topicRecords = {};

        // 只使用窗口内的日期（5个日期）
        windowDates.forEach(date => {
            const data = getCachedData(date);

            if (data && data.records) {
                dateRecords[date] = data;

                data.records.forEach(record => {
                    const topic = record.topic;

                    if (!topicRecords[topic]) {
                        topicRecords[topic] = {
                            topic: topic,
                            dates: {},
                            stages: {}
                        };
                    }

                    topicRecords[topic].dates[date] = record.content;
                    topicRecords[topic].stages[date] = record.stage;
                });
            }
        });

        // 获取窗口内所有题材
        const allTopics = Object.keys(topicRecords);

        // 过滤掉5天都没有数据的题材
        const activeTopics = filterInactiveTopics(allTopics);

        // 按全局顺序排序，并更新全局顺序（追加新题材）
        updateTopicOrder(activeTopics);
        const sortedActiveTopics = sortTopicsByOrder(activeTopics);

        // 按排序后的顺序生成 topicData
        const sortedTopicData = sortedActiveTopics.map(topicName => {
            const topicData = {
                topic: topicName,
                days: {},
                stages: {}
            };

            windowDates.forEach(date => {
                const content = topicRecords[topicName]?.dates[date];
                if (content !== undefined) {
                    topicData.days[date] = content;
                }
                const stage = topicRecords[topicName]?.stages[date];
                if (stage !== undefined) {
                    topicData.stages[date] = stage;
                }
            });

            return topicData;
        });


        // windowDates 已经是窗口内的5个日期，直接使用，不需要再slice
        // 将windowDates转为新数组，确保可以正确计算 slice
        const windowDatesArray = Array.from(windowDates);

        return {
            dates: windowDatesArray.map(date => ({
                date: date,        // 绝对日期
                label: formatDateWithWeekday(date),  // 带星期几的格式化日期
                // 为了兼容，保留旧的字段
                day: null,         // 废弃字段
                fullDate: date     // 兼容旧代码
            })),
            dateRecords: dateRecords,
            topicData: sortedTopicData
        };
    }

    // 格式化日期，包含星期几
    function formatDateWithWeekday(dateStr) {
        const date = new Date(dateStr);
        const weekdays = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
        const weekday = weekdays[date.getDay()];
        const month = date.getMonth() + 1;
        const day = date.getDate();
        return `${month}-${day}(${weekday})`;
    }

    // 获取题材顺序的localStorage key
    function getTopicOrderKey() {
        return 'fupan_topic_order_v3';
    }

    // 从 localStorage 加载话题顺序
    function loadTopicOrder() {
        try {
            const key = getTopicOrderKey();
            const saved = localStorage.getItem(key);
            if (saved) {
                const order = JSON.parse(saved);
                return order;
            }
        } catch (error) {
            console.warn('加载话题顺序失败:', error);
        }
        return null;
    }

    // 保存话题顺序到 localStorage
    function saveTopicOrder(order) {
        try {
            const key = getTopicOrderKey();
            localStorage.setItem(key, JSON.stringify(order));
        } catch (error) {
            console.warn('保存话题顺序失败:', error);
        }
    }

    // 获取窗口内所有题材（去重）
    function getTopicsInWindow() {
        const topics = new Set();
        const windowDates = getCurrentWindowDates();

        windowDates.forEach(date => {
            const data = getCachedData(date);

            if (data && data.records) {
                data.records.forEach(record => {
                    if (record.topic) {
                        topics.add(record.topic);
                    }
                });
            }
        });

        return Array.from(topics);
    }

    // 过滤窗口内5天都没有数据的题材
    function filterInactiveTopics(topics) {
        const windowDates = getCurrentWindowDates();
        const activeTopics = [];

        topics.forEach(topicName => {
            let hasActivity = false;
            windowDates.forEach(date => {
                const data = getCachedData(date);

                if (data && data.records) {
                    const hasRecord = data.records.some(record => record.topic === topicName);
                    if (hasRecord) {
                        hasActivity = true;
                    }
                }
            });

            if (hasActivity) {
                activeTopics.push(topicName);
            }
        });

        return activeTopics;
    }

    // 按全局顺序排列题材，新题材插入到正确位置
    function sortTopicsByOrder(topics) {
        if (state.topicOrder.length === 0) {
            return topics;
        }

        const sorted = topics.sort((a, b) => {
            const indexA = state.topicOrder.indexOf(a);
            const indexB = state.topicOrder.indexOf(b);

            if (indexA === -1 && indexB === -1) {
                return 0;
            }
            if (indexA === -1) {
                return 1;
            }
            if (indexB === -1) {
                return -1;
            }

            return indexA - indexB;
        });

        return sorted;
    }

    // 更新全局顺序（追加新题材）
    function updateTopicOrder(topics) {
        const newTopics = topics.filter(topic => !state.topicOrder.includes(topic));

        if (newTopics.length > 0) {
            state.topicOrder.push(...newTopics);
            saveTopicOrder(state.topicOrder);
        }

        return state.topicOrder;
    }

    // 导出接口
    window.SlidingWindow = {
        init: initialize,
        moveLeft: moveWindowLeft,
        moveRight: moveWindowRight,
        scrollTo: scrollTo,
        getCurrentWindow: () => {
            const windowDates = getCurrentWindowDates();
            return windowDates.map(date => ({
                date: date,
                label: date,
                day: null  // 不再使用 day
            }));
        },
        getCurrentWindowData: getCurrentWindowData,
        getState: () => state,
        getAvailableDates: () => state.availableDates,
        isInitialized: () => state.isInitialized
    };
})();
