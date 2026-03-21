"""
A component which allows you to parse http://www.qiyoujiage.com/ get oil price
Support individual sensors and history trend.
"""
import re
import logging
import datetime
import voluptuous as vol
from homeassistant.helpers.entity import Entity
import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import CONF_NAME, CONF_REGION
import requests
from bs4 import BeautifulSoup

__version__ = '0.2.1'
_LOGGER = logging.getLogger(__name__)

REQUIREMENTS = ['requests', 'beautifulsoup4']

SCAN_INTERVAL = datetime.timedelta(hours=1)
ICON = 'mdi:gas-station'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_NAME): cv.string,
    vol.Required(CONF_REGION): cv.string,
})

async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the oil price sensors."""
    _LOGGER.info("Setting up OilPrice sensors")
    
    region = config[CONF_REGION]
    base_name = config[CONF_NAME]
    
    # 核心：创建一个共享的数据更新器
    data_updater = OilDataUpdater(hass, region)
    
    # 初始化时先更新一次数据
    await data_updater.async_update()
    
    sensors = []
    # 创建 4 个独立油价传感器 (92, 95, 98, 0)
    for oil_type in ["92", "95", "98", "0"]:
        sensors.append(OilPriceIndividualSensor(data_updater, f"{base_name}_{oil_type}", region, oil_type))
    
    # 创建 1 个汇总描述传感器
    sensors.append(OilPriceSummarySensor(data_updater, f"{base_name}_summary", region))
    
    async_add_devices(sensors, True)


class OilDataUpdater:
    """数据抓取协调器：负责统一抓取网页并解析，供所有子传感器使用"""
    def __init__(self, hass, region):
        self.hass = hass
        self.region = region
        self.data = {}

    async def async_update(self):
        """异步更新数据"""
        self.data = await self.hass.async_add_executor_job(self._fetch_data)

    def _fetch_data(self):
        """在线程池中执行同步网络请求"""
        try:
            _LOGGER.debug(f"Fetching oil prices for {self.region}")
            header = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36'
            }
            url = f'http://www.qiyoujiage.com/{self.region}.shtml'
            response = requests.get(url, headers=header, timeout=15)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, "lxml")
            
            # 解析具体价格
            dls = soup.select("#youjia > dl")
            prices = {}
            for dl in dls:
                dt_text = dl.select('dt')[0].text
                match = re.search(r"\d+", dt_text)
                if match:
                    key = match.group()
                    prices[key] = dl.select('dd')[0].text
            
            # 解析汇总状态和提示信息
            summary_div = soup.select("#youjiaCont > div")
            summary = summary_div[1].contents[0].strip() if len(summary_div) > 1 else "未知"
            
            tips_span = soup.select("#youjiaCont > div:nth-of-type(2) > span")
            tips = tips_span[0].text.strip() if tips_span else ""
            
            return {
                "prices": prices,
                "summary": summary,
                "tips": tips,
                "time": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        except Exception as e:
            _LOGGER.error(f"Error fetching oil price data: {e}")
            return self.data  # 失败时返回旧数据


class OilPriceIndividualSensor(Entity):
    """单个油价传感器类"""
    def __init__(self, updater, name, region, oil_type):
        self._updater = updater
        self._name = name
        self._region = region
        self._oil_type = oil_type

    @property
    def name(self): return self._name

    @property
    def state(self):
        prices = self._updater.data.get("prices", {})
        # 尝试将价格转为浮点数，以便 HA 生成折线图
        val = prices.get(self._oil_type, "unknown")
        try:
            return float(val)
        except:
            return val

    @property
    def unit_of_measurement(self): return "元/升"

    @property
    def icon(self): return ICON

    @property
    def state_class(self): return "measurement"

    @property
    def extra_state_attributes(self):
        return {
            "region": self._region,
            "oil_type": self._oil_type,
            "tips": self._updater.data.get("tips"),
            "update_time": self._updater.data.get("time")
        }

    @property
    def unique_id(self): return f"oil_price_{self._region}_{self._oil_type}"

    async def async_update(self):
        """调用更新器的更新方法"""
        await self._updater.async_update()


class OilPriceSummarySensor(Entity):
    """汇总传感器类"""
    def __init__(self, updater, name, region):
        self._updater = updater
        self._name = name
        self._region = region

    @property
    def name(self): return self._name

    @property
    def state(self): return self._updater.data.get("summary")

    @property
    def icon(self): return ICON

    @property
    def extra_state_attributes(self):
        attrs = self._updater.data.get("prices", {}).copy()
        attrs["tips"] = self._updater.data.get("tips")
        attrs["update_time"] = self._updater.data.get("time")
        return attrs

    @property
    def unique_id(self): return f"oil_price_{self._region}_summary"

    async def async_update(self):
        await self._updater.async_update()