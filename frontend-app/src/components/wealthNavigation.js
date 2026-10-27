export const WEALTH_PAGES=[['wealth','理财产品'],['funds','基金份额'],['deposits','定期存款'],['gold','黄金交易'],['gold-savings','黄金定投'],['exchange','外币账户']];
export const isWealthPage=route=>WEALTH_PAGES.some(([key])=>key===route);
