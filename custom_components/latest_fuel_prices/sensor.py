"""
A component which allows you to parse oil prices from multiple sources.
Trends from qiyoujiage.com, Real-time prices from icauto.com.cn
"""
import re
import asyncio
import logging
import datetime
import voluptuous as vol
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA, 
    SensorEntity, 
    SensorStateClass, 
    SensorDeviceClass
)
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_NAME, CONF_REGION
import requests
from bs4 import BeautifulSoup

__version__ = '0.3.5'
_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['requests', 'beautifulsoup4', 'lxml']

SCAN_INTERVAL = datetime.timedelta(hours=6)
ICON = 'mdi:gas-station'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_REGION): cv.string,
    vol.Required("city"): cv.string,
})

async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the oil price sensors."""
    _LOGGER.info("Setting up Hybrid OilPrice sensors (Restoring measurement class)")
    
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
        self._last_update = None
        self._lock = asyncio.Lock()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
        }

    async def async_update(self):
        async with self._lock:
            now = datetime.datetime.now()
            if self._last_update and (now - self._last_update).total_seconds() < 60:
                return

            _LOGGER.debug(f"Fetching hybrid oil data for {self.region} / {self.city_code}")
            result = await self.hass.async_add_executor_job(self._fetch_all_data)
            if result:
                self.data = result
                self._last_update = now

    def _fetch_all_data(self):
        data = {"prices": {}, "summary": "未知", "tips": "", "time": ""}
        
        # --- 源 1: 趋势 (qiyoujiage.com) ---
        try:
            url_trend = f'http://www.qiyoujiage.com/{self.region}.shtml'
            res_trend = requests.get(url_trend, headers=self.headers, timeout=10)
            res_trend.encoding = 'utf-8'
            soup_trend = BeautifulSoup(res_trend.text, "lxml")
            
            summary_divs = soup_trend.select("#youjiaCont > div")
            if len(summary_divs) >= 2:
                target_div = summary_divs[1]
                tips_span = target_div.find("span")
                raw_tips_text = tips_span.get_text(strip=True) if tips_span else ""
                clean_tips = raw_tips_text.replace("，当前微信公众号油价已更新。", "").replace("当前微信公众号油价已更新。", "").strip()
                if clean_tips and not clean_tips.endswith("。"): clean_tips += "。"
                data["tips"] = clean_tips
                full_text = target_div.get_text(strip=True)
                summary = full_text.replace(raw_tips_text, "").strip()
                if summary and not summary.endswith("。"): summary += "。"
                data["summary"] = summary
        except Exception as e:
            _LOGGER.warning(f"Error fetching trend data: {e}")

        # --- 源 2: 实时价格 (icauto.com.cn 历史表解析) ---
        try:
            url_price = f'https://www.icauto.com.cn/oil/{self.city_code}.html'
            res_price = requests.get(url_price, headers=self.headers, timeout=10)
            res_price.encoding = 'utf-8'
            soup_price = BeautifulSoup(res_price.text, "lxml")
            
            table = soup_price.find("table")
            if table:
                rows = table.find_all("tr")
                if len(rows) >= 2:
                    latest_row = rows[1]
                    tds = latest_row.find_all("td")
                    if len(tds) >= 7:
                        data["prices"]["92"] = tds[2].get_text(strip=True)
                        data["prices"]["95"] = tds[4].get_text(strip=True)
                        data["prices"]["98"] = tds[5].get_text(strip=True)
                        data["prices"]["0"]  = tds[6].get_text(strip=True)
        except Exception as e:
            _LOGGER.error(f"Error parsing icauto table: {e}")

        data["time"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return data


class OilPriceIndividualSensor(SensorEntity):
    def __init__(self, updater, name, region, oil_type):
        self._updater = updater
        self._attr_name = name
        self._region = region
        self._oil_type = oil_type
        self._attr_icon = ICON
        self._attr_native_unit_of_measurement = "元/升"
        
        self._attr_device_class = None 
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
            "update_time": self._updater.data.get("time")
        }

    async def async_update(self):
        await self._updater.async_update()


class OilPriceSummarySensor(Entity):
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
        attrs = self._updater.data.get("prices", {}).copy()
        attrs["tips"] = self._updater.data.get("tips")
        attrs["update_time"] = self._updater.data.get("time")
        return attrs

    async def async_update(self):
        await self._updater.async_update()