// The sidebar keeps the main categories; each category page carries its own tabs.
export const GROUPS = {
  overview: [["overview", "总览"], ["todos", "统一待办"]],
  bills: [["bills", "账单与订阅"], ["credit-account", "信用账单"], ["credit", "贷款与分期"]],
  wealth: [["wealth", "理财产品"], ["funds", "基金份额"], ["deposits", "定期存款"], ["gold", "黄金交易"], ["gold-savings", "黄金定投"], ["exchange", "外币账户"]],
  life: [["life", "生活服务"], ["plans", "活动计划"], ["support", "服务工单"]],
};
export const groupOf = (route) => Object.keys(GROUPS).find((g) => GROUPS[g].some(([key]) => key === route)) || null;
