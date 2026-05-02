"""
A component which allows you to parse oil prices from multiple sources.
Trends from qiyoujiage.com, Real-time prices from icauto.com.cn & qiyoujiage.com
"""
import re
import asyncio
import logging
import datetime
import os
import json
import voluptuous as vol
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA, 
    SensorEntity, 
    SensorStateClass, 
)
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME, CONF_REGION
import requests
from bs4 import BeautifulSoup

__version__ = '0.4.4'
_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['requests', 'beautifulsoup4', 'lxml']

SCAN_INTERVAL = datetime.timedelta(minutes=1)
ICON = 'mdi:gas-station'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_REGION): cv.string,
    vol.Required("city"): cv.string,
})

async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the oil price sensors."""
    _LOGGER.info("Setting up Hybrid OilPrice sensors (Integrated Improved Parser)")
    
    region = config[CONF_REGION]
    city_code = config["city"]
    base_name = config[CONF_NAME]
    
    data_updater = OilDataUpdater(hass, region, city_code)
    
    await data_updater.async_update()
    
    sensors = []
    for oil_type in ["92", "95", "98", "0"]:
        sensors.append(OilPriceIndividualSensor(data_updater, f"{base_name}_{oil_type}", region, oil_type))
    
    sensors.append(OilPriceSummarySensor(data_updater, f"{base_name}_summary", region))
    
    async_add_devices(sensors, True)


class OilDataUpdater:
    def __init__(self, hass, region, city_code):
        self.hass = hass
        self.region = region
        self.city_code = city_code
        self.data = {} 
        self._last_prices = {} 
        self._last_update = None
        self._lock = asyncio.Lock()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        }
        
        # 增加持久化缓存文件路径
        self._cache_file = hass.config.path(".oil_price_cache.json")
        self._load_last_prices()

    def _load_last_prices(self):
        """从文件中加载上一次的缓存价格，防止重启丢失"""
        if os.path.exists(self._cache_file):
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    self._last_prices = json.load(f)
            except Exception as e:
                _LOGGER.warning(f"无法读取油价缓存文件: {e}")

    def _save_last_prices(self, prices):
        """将价格保存到文件中"""
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(prices, f, ensure_ascii=False)
        except Exception as e:
            _LOGGER.warning(f"无法写入油价缓存文件: {e}")

    async def async_update(self):
        async with self._lock:
            now = datetime.datetime.now()
            
            # 定时检查更新 (1:00, 6:00)
            update_times = [(1, 0), (6, 0)]
            is_time_to_update = any(now.hour == h and now.minute == m for h, m in update_times)

            if self.data and not is_time_to_update:
                return

            _LOGGER.info(f"正在抓取双源数据并比对最新油价...")
            result = await self.hass.async_add_executor_job(self._fetch_and_compare_data)
            
            if result and result.get("prices"):
                # 如果当前已有数据，在覆盖前先存入 _last_prices 用于下次比对
                if self.data.get("prices"):
                    self._last_prices = self.data["prices"]
                
                self.data = result
                self._last_update = now
                
                # 持久化存储
                self._save_last_prices(self.data["prices"])
                
                _LOGGER.info(f"更新成功，采用源: {result.get('source_log')}")
            else:
                _LOGGER.warning("抓取失败，未获取到有效油价数据")

    def _fetch_and_compare_data(self):
        """核心比对逻辑 (Qiyoujiage 优先，滞后自动降级)"""
        res1 = self._get_qiyoujiage_data()  # 主数据源：qiyoujiage
        res2 = self._get_icauto_data()      # 备用数据源：icauto

        final_prices = {}
        source_selected = "none"

        p1 = res1.get("prices", {})
        p2 = res2.get("prices", {})
        old_p = self._last_prices

        # 逻辑判断：
        # 情况 A: qiyoujiage 能够正常获取且数据有更新 (以 qiyoujiage 为准)
        p1_changed = old_p and p1 != old_p
        
        if p1 and any(p1.values()):
            # 如果是第一次运行，或者 qiyoujiage 数据发生了变化，直接优先使用
            if not old_p or p1_changed:
                final_prices = p1
                source_selected = "qiyoujiage (已更新)"
            else:
                # 情况 B: qiyoujiage 没有更新 (仍处于旧周期)，此时检查 icauto 是否发生了变化（说明 icauto 进入了新周期）
                p2_changed = old_p and p2 != old_p
                
                if p2 and any(p2.values()) and p2_changed:
                    # icauto 更新了新数据，而 qiyoujiage 还在滞后，采用 icauto 的数据
                    final_prices = p2
                    source_selected = "icauto (qiyoujiage滞后，临时过渡)"
                else:
                    # 两个数据源均未变化，或都无法判断，默认使用 qiyoujiage
                    final_prices = p1
                    source_selected = "qiyoujiage (保持旧数据)"
        elif p2 and any(p2.values()):
            # 如果 qiyoujiage 获取失败，直接回退使用 icauto
            final_prices = p2
            source_selected = "icauto (qiyoujiage失效)"
        
        return {
            "prices": final_prices,
            "summary": res1.get("summary", "未知"),
            "tips": res1.get("tips", ""),
            "time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "source_log": source_selected
        }

    def _get_qiyoujiage_data(self):
        """解析 qiyoujiage.com"""
        res_data = {"prices": {}, "summary": "未知", "tips": ""}
        try:
            url = f'http://www.qiyoujiage.com/{self.region}.shtml'
            r = requests.get(url, headers=self.headers, timeout=15)
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, "lxml")
            dls = soup.select("#youjia > dl")
            for dl in dls:
                dts = dl.select('dt')
                dds = dl.select('dd')
                if dts and dds:
                    dt_text = dts[0].text
                    match = re.search(r"\d+", dt_text)
                    if match:
                        key = match.group()
                        res_data["prices"][key] = dds[0].text.strip()
            summary_divs = soup.select("#youjiaCont > div")
            if len(summary_divs) >= 2:
                target_div = summary_divs[1]
                tips_span = target_div.find("span")
                raw_tips_text = ""
                if tips_span:
                    raw_tips_text = tips_span.get_text(strip=True)
                    clean_tips = raw_tips_text.replace("，当前微信公众号油价已更新。", "").replace("当前微信公众号油价已更新。", "").strip()
                    if clean_tips and not clean_tips.endswith("。"):
                        clean_tips += "。"
                    res_data["tips"] = clean_tips
                full_text = target_div.get_text(strip=True)
                summary = full_text.replace(raw_tips_text, "").strip()
                if summary and not summary.endswith("。"):
                    summary += "。"
                res_data["summary"] = summary
        except Exception as e:
            _LOGGER.warning(f"Error fetching from qiyoujiage: {e}")
        return res_data

    def _get_icauto_data(self):
        """解析 icauto.com.cn 的价格"""
        res_data = {"prices": {}}
        try:
            url = f'https://www.icauto.com.cn/oil/{self.city_code}.html'
            r = requests.get(url, headers=self.headers, timeout=15)
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, "lxml")
            table = soup.find("table")
            if table:
                rows = table.find_all("tr")
                if len(rows) >= 2:
                    latest_row = rows[1]
                    tds = latest_row.find_all("td")
                    if len(tds) >= 7:
                        res_data["prices"]["92"] = tds[2].get_text(strip=True)
                        res_data["prices"]["95"] = tds[4].get_text(strip=True)
                        res_data["prices"]["98"] = tds[5].get_text(strip=True)
                        res_data["prices"]["0"]  = tds[6].get_text(strip=True)
        except Exception as e:
            _LOGGER.warning(f"Error fetching from icauto: {e}")
        return res_data


class OilPriceIndividualSensor(SensorEntity):
    def __init__(self, updater, name, region, oil_type):
        self._updater = updater
        self._attr_name = name
        self._region = region
        self._oil_type = oil_type
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = "元/升"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_unique_id = f"oil_price_{region}_{oil_type}"

    @property
    def native_value(self):
        prices = self._updater.data.get("prices", {})
        val = prices.get(self._oil_type)
        if val:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None
        return None

    @property
    def extra_state_attributes(self):
        return {
            "region": self._region,
            "oil_type": self._oil_type,
            "tips": self._updater.data.get("tips"),
            "source": self._updater.data.get("source_log"),
            "update_time": self._updater.data.get("time")
        }

    async def async_update(self):
        await self._updater.async_update()


class OilPriceSummarySensor(SensorEntity):
    def __init__(self, updater, name, region):
        self._updater = updater
        self._attr_name = name
        self._region = region
        self._attr_icon = ICON
        self._attr_unique_id = f"oil_price_{region}_summary"

    @property
    def state(self):
        return self._updater.data.get("summary")

    @property
    def extra_state_attributes(self):
        attrs = (self._updater.data.get("prices") or {}).copy()
        attrs["tips"] = self._updater.data.get("tips")
        attrs["source"] = self._updater.data.get("source_log")
        attrs["update_time"] = self._updater.data.get("time")
        return attrs

    async def async_update(self):
        await self._updater.async_update()