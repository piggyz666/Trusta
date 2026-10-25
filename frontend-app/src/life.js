// Third-party entry points for 生活服务. TRUSTA only links out; nothing is bought or paid here.
export const LIFE_HOSTS = new Set(["flights.ctrip.com", "hotels.ctrip.com", "kyfw.12306.cn", "www.12306.cn", "m.damai.cn", "show.maoyan.com",
  "www.maoyan.com", "dianying.taobao.com", "www.95598.cn", "www.10086.cn", "www.10010.com", "www.189.cn", "si.12333.gov.cn",
  "fuwu.nhsa.gov.cn", "www.mohurd.gov.cn"]);

export const safeLink = (url) => { try { const u = new URL(url); return u.protocol === "https:" && LIFE_HOSTS.has(u.host); } catch { return false; } };

export const LIFE_SECTIONS = [
  { key: "FLIGHT", title: "机票", icon: "plane", ask: ["帮我查下周五北京到上海的机票", "11月6号北京飞成都，9号回来"],
    links: [["携程机票", "https://flights.ctrip.com/"]] },
  { key: "TRAIN", title: "火车票", icon: "train", ask: ["明天上海到杭州的高铁", "下周六广州到深圳的火车票"],
    links: [["中国铁路 12306", "https://www.12306.cn/"]] },
  { key: "HOTEL", title: "酒店", icon: "bed", ask: ["帮我订三亚的酒店，11月10号住3晚", "下周五成都的酒店"],
    links: [["携程酒店", "https://hotels.ctrip.com/"]] },
  { key: "SHOW", title: "演出", icon: "ticket", ask: ["上海最近有什么演唱会", "11月杭州的音乐节"],
    links: [["大麦", "https://m.damai.cn/"], ["猫眼演出", "https://show.maoyan.com/"]] },
  { key: "MOVIE", title: "电影", icon: "ticket", ask: ["这周末想看电影"],
    links: [["猫眼电影", "https://www.maoyan.com/"], ["淘票票", "https://dianying.taobao.com/"]] },
  { key: "UTILITY", title: "生活缴费", icon: "bolt", ask: ["交一下电费", "充话费"],
    links: [["网上国网（电费）", "https://www.95598.cn/"], ["中国移动", "https://www.10086.cn/"], ["中国联通", "https://www.10010.com/"], ["中国电信", "https://www.189.cn/"]] },
  { key: "PUBLIC", title: "社保医保", icon: "shield", ask: ["社保怎么查", "医保余额在哪看"],
    links: [["国家社会保险公共服务平台", "https://si.12333.gov.cn/"], ["国家医保服务平台", "https://fuwu.nhsa.gov.cn/"]] },
];
