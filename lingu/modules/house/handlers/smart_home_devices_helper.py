from typing import Tuple, Dict, List
from lingu import log
import threading
import tinytuya
import colorsys
import time
import json
import re


class SmartBulbThread(threading.Thread):
    def __init__(
            self,
            bulb_param: Dict[str, str],
            connection_event: threading.Event,
            status_event: threading.Event,
            action_condition: threading.Condition) -> None:
        super().__init__()
        self.action_condition = action_condition
        self.color = (0, 0, 0)
        self.new_color = (0, 0, 0)
        self.bulb_param = bulb_param
        self.connection_event = connection_event
        self.status_event = status_event
        self.error = False
        self.error_message = ""
        self.state = 'off'
        self.new_state = 'off'
        self.is_running = True
        self.last_state_time = time.time()

    def set_bulb_on_off(
            self,
            state: bool) -> None:
        with self.action_condition:
            self.new_state = 'on' if state else 'off'
            if self.new_state == 'on' and self.color == (0, 0, 0):
                self.new_color = (127, 127, 127)

            log.low(f'  [house] thread of {self.bulb_param["name"]} '
                    'set to {state}')
            self.action_condition.notify()

    def get_bulb_on_off_state(self) -> str:
        return self.state

    def get_error(self) -> str:
        return self.error

    def get_color(self) -> Tuple[int, int, int]:
        return self.color

    def change_color(
            self,
            color: Tuple[int, int, int]) -> None:
        with self.action_condition:
            self.new_color = color
            self.action_condition.notify()

    def shutdown(self) -> None:
        self.is_running = False

    def set_color(
            self,
            rgb_tupel: Tuple[int, int, int]) -> None:
        self.color = rgb_tupel
        self.new_color = rgb_tupel
        log.low(f'  [house] {self.bulb_param["name"]} color set to: {rgb_tupel}')

        if self.state == 'off' and (rgb_tupel[0] != 0 or rgb_tupel[1] != 0 or rgb_tupel[2] != 0):
            log.low(f'  [house] {self.bulb_param["name"]} device state turned on')
            self.device.turn_on(nowait=False)

        self.device.set_colour(rgb_tupel[0], rgb_tupel[1], rgb_tupel[2], nowait=False)        
        
        # we don't ever flood the bulb, so safest bet is forcewait after set color
        time.sleep(0.2) 

    def get_state(self):
        data = self.device.status()
        if 'dps' in data:
            dps = data['dps']
            if '24' in dps:
                color = dps['24']
                self.color = self.hsv_string_to_rgb(color)

        state = self.device.state()
        self.state = 'on' if('is_on' in state and (state['is_on'])) else 'off'
        if 'Error' in state or 'error' in state:
            if not self.error:
                log.wrn(f'  [house] 💡 error {self.bulb_param["name"]}: '
                        f' {state} | data {data}')
            self.error = True
            self.error_message = state['Error'] if 'Error' in state else state['error']

        else:
            if self.error:
                log.dbg(f'  [house] 💡 error {self.bulb_param["name"]} cleared:'
                        f' {state} | data {data}')
            # else:
            #     log.dbg(f'  [house] 💡 {self.bulb_param["name"]} fine:'
            #             f' {state} | data {data}')
            self.error = False
            self.error_message = ""

        #log.dbg(f'  [house] 💡 state {self.bulb_param["name"]}: {state} | data {data}')
        return data, state

    def run(self) -> None:
        name = self.bulb_param["name"]
        id = self.bulb_param['id']
        ip = self.bulb_param['ip']
        key = self.bulb_param['key']
        log.dbg(f"  [house] 💡 trying to connect to name {name}, id {id}, ip {ip}, key {key}")
        deviceVersion = float(self.bulb_param['version'])
        # Connect to tuya bulb
        self.device = tinytuya.BulbDevice(id, ip, key)
        self.device.set_version(deviceVersion)
        # log.low(f'  [house] 💡 {name} connected')        
        self.connection_event.set()
        # Get status of bulb    
        data, state = self.get_state()

        # log.dbg(f"  [house] 💡 {name} state: {data}")
        self.state = 'on' if('is_on' in state and (state['is_on'])) else 'off'

        self.new_color = self.color
        self.new_state = self.state
        log.low(f'  [house] {name} color: {self.color}, bulb state: {self.state}')
        self.status_event.set()
        # Enter worker cycle
        while self.is_running:
            with self.action_condition:
                action_condition_change = self.action_condition.wait(timeout=0.5)
                current_time = time.time()
                if current_time - self.last_state_time >= 5:
                    self.get_state()
                    self.last_state_time = current_time
                if action_condition_change:
                    log.low(f'  [house] notified action_condition_change in thread for bulb {self.bulb_param["name"]}')
                    log.low(f'  [house] {self.bulb_param["name"]} self.new_color {self.new_color} self.color {self.color} self.new_state {self.new_state} self.state {self.state}')
                    if self.new_color != self.color:
                        log.low(f'  [house] self.new_color was new, setting {self.new_color}')
                        self.set_color(self.new_color)
                    if self.new_state != self.state:
                        log.low(f'  [house] self.new_state was new, setting {self.new_state}')
                        if self.new_state == 'on':
                            self.device.turn_on()
                            self.state = 'on'
                            log.low(f'  [house] {self.bulb_param["name"]} turned on')
                        elif self.new_state == 'off':
                            self.device.turn_off()
                            self.state = 'off'
                            log.low(f'  [house] {self.bulb_param["name"]} turned off')

                time.sleep(0.01)

    def hsv_string_to_rgb(self,
            hsv: str) -> Tuple[int, int, int]:
        color_value = int(hsv[0:4], 16)
        saturation_value = int(hsv[4:8], 16)
        brightness_value = int(hsv[8:12], 16) 
        # convert to values between 0 and 1
        color_value = color_value / 360.0
        saturation_value = saturation_value / 1000.0
        brightness_value = brightness_value / 1000.0
        # calculate rgb from hsv values
        hsv = (color_value, saturation_value, brightness_value)
        rgb = colorsys.hsv_to_rgb(*hsv)
        return (int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))

class LightManager():
    def __init__(self, bulbs) -> None:
        self.bulb_params = bulbs                        
        self.is_running = True
        self.is_rotation_active = False
        self.rotation_speed = 10
        self.rotation_update_pause = 0.5
        self.rotation_clockwise = True
        self.bulb_devices = []
        self.bulb_states = []
        self.bulb_colors = []
        self.bulb_rotation_colors = []
        self.bulbs_ready = False
        num_lamps = len(self.bulb_params)
        # Create event objects
        self.connection_events = [threading.Event() for _ in range(num_lamps)]
        self.status_events = [threading.Event() for _ in range(num_lamps)]
        action_conditions = [threading.Condition() for _ in range(num_lamps)]

        self.bulb_threads = [SmartBulbThread(self.bulb_params[i], self.connection_events[i], self.status_events[i], action_conditions[i]) for i in range(num_lamps)]        
        
        for thread in self.bulb_threads:
            self.bulb_colors.append((0,0,0))
            thread.start()
        self.rotation_worker_thread = threading.Thread(target=self.rotation_worker)
        self.rotation_worker_thread.start()

    def set_bulb_on_off(self, 
                        bulb_name: str, 
                        state: bool) -> None:
        for bulb_thread in self.bulb_threads:
            if bulb_thread.bulb_param['name'] == bulb_name:
                log.inf(f'  [house] setting {bulb_name} on off state to {state}')
                bulb_thread.set_bulb_on_off(state)
                return {
                    "result": "success",
                    "state": state,
                }

        bulb_names = [b["name"] for b in self.bulb_params]
        bulb_names_string = ', '.join(bulb_names)
        return {
            "result": "error",
            "reason": f"bulb name {bulb_name} not found, must be one of these: {bulb_names_string}",
        }

    def is_error(self, bulb_name: str) -> bool:
        for bulb_thread in self.bulb_threads:
            if bulb_thread.bulb_param['name'] == bulb_name:
                return bulb_thread.get_error()

    def get_bulb_on_off_state(self, bulb_name: str) -> str:
        for bulb_thread in self.bulb_threads:
            if bulb_thread.bulb_param['name'] == bulb_name:
                return {
                    "result": "success",
                    "state" : bulb_thread.get_bulb_on_off_state(),
                    "name" : bulb_name
                }
        bulb_names = [b["name"] for b in self.bulb_params]
        bulb_names_string = ', '.join(bulb_names)
        return {
            "result": "error",
            "reason" : f"bulb name {bulb_name} not found, must be one of these: {bulb_names_string}",
        }        

    # def set_bulb_on_off(self, 
    #                     bulb_names: List[str], 
    #                     state: bool) -> None:
    #     results = []
    #     for bulb_name in bulb_names:
    #         for bulb_thread in self.bulb_threads:
    #             if bulb_thread.bulb_param['name'] == bulb_name:
    #                 bulb_thread.set_bulb_on_off(state)
    #                 results.append({
    #                     "result": "success",
    #                     "state" : state,
    #                     "name" : bulb_name
    #                 })
    #                 break
    #         else:
    #             results.append({
    #                 "result": "error",
    #                 "reason" : "bulb name not found",
    #                 "name" : bulb_name
    #             })
    #     return results

    # def get_bulb_on_off_state(self, bulb_names: List[str]) -> str:
    #     results = []
    #     for bulb_name in bulb_names:
    #         for bulb_thread in self.bulb_threads:
    #             if bulb_thread.bulb_param['name'] == bulb_name:
    #                 results.append({
    #                     "result": "success",
    #                     "state" : bulb_thread.get_bulb_on_off_state(),
    #                     "name" : bulb_name
    #                 })
    #                 break
    #         else:
    #             results.append({
    #                 "result": "error",
    #                 "reason" : "bulb name not found",
    #                 "name" : bulb_name
    #             })
    #     return results

    def shutdown(self) -> None:
        log.low('  [house] shutting down bulb threads')
        self.is_running = False
        for thread in self.bulb_threads:
            thread.shutdown()
        # Wait for all threads to finish
        for thread in self.bulb_threads:
            thread.join()

    def wait_ready(self) -> None:
        # Wait for all bulbs connected
        log.low('  [house] connecting to bulbs')
        for event in self.connection_events:
            event.wait()
        log.inf('  [house] bulbs connected')
        # Wait for all bulbs status requested
        for event in self.status_events:
            event.wait()
        log.inf('  [house] bulbs status check complete')
        # Init colors
        for bulb_thread in self.bulb_threads:
            i = self.find_bulb_index(bulb_thread.bulb_param['name'])
            self.bulb_colors[i] = bulb_thread.color
        self.bulbs_ready = True

    def set_colors_json_hex_string(self, 
            json: str) -> None:
        data = json.loads(json)
        self.set_colors_json_hex(data)

    def find_bulb_index(self, 
            name: str) -> int:
        for bulb in self.bulb_params:
            if bulb['name'] == name:
                index = self.bulb_params.index(bulb)
                return index
        return -1

    def is_valid_hex_color(self, s):
        """Check if a string is a valid RGB Hex color."""
        if re.match("^#[0-9a-fA-F]{6}$", s):
            return True
        else:
            return False
            
    def convert_from_hex(self, hex_color_str):
        """Convert a hexadecimal color string to a RGB tuple."""
        
        # remove the '#' at the beginning if present
        if hex_color_str.startswith('#'):
            hex_color_str = hex_color_str[1:]

        # convert hex to decimal
        red = int(hex_color_str[0:2], 16)
        green = int(hex_color_str[2:4], 16)
        blue = int(hex_color_str[4:6], 16)

        return (red, green, blue)

    def set_color_hex(self,
            bulb_name: str,
            color_string: str) -> None:

        if not self.is_valid_hex_color(color_string):
            log.low(f'  [house] color string {color_string.name} is not hex string')
            return {
                "result": "error",
                "reason" : "color must be hex string like #C0C0C0"
            }            

        color = self.convert_from_hex(color_string)
        return self.set_color(bulb_name, color)

    # def raise_color_information_event(self):
    #     self.get_colors()
        ### self.server.set_event("bulb_colors_changed", self.get_colors_json_rgb())

    def set_color(self,
                  bulb_name: str,
                  color: Tuple[int, int, int]) -> None:

        i = self.find_bulb_index(bulb_name)

        log.low(f'  [house] set_color in manager called for {bulb_name}')        
        if i == -1:
            bulb_names = [b["name"] for b in self.bulb_params]
            bulb_names_string = ', '.join(bulb_names)
            log.low(f'  [house] bulb name {bulb_name} not found')     
            return {
                "result": "error",
                "reason": f"bulb name {bulb_name} not found, "
                          f"must be one of these: {bulb_names_string}",
            }

        self.bulb_colors[i] = color
        for bulb_thread in self.bulb_threads:
            if bulb_thread.bulb_param['name'] == bulb_name:
                log.low(f'  [house] change_color for thread from manager for bulb {bulb_name} called')
                bulb_thread.change_color(color)
                time.sleep(0.25)
                bulb_thread.get_state()
                if bulb_thread.error:
                    bulb_thread.error_message
                    return {
                        "result": "error",
                        "reason": f"bulb {bulb_name} error: "
                                  f"{bulb_thread.error_message}",
                    }

                return {
                    "result": "success",
                    "state": color,
                }        
        return {
            "result": "error",
            "reason": f"bulb thread for bulb {bulb_name} not found",
        }

    def get_color_hex(self, 
            bulb_name: str) -> str:
        color = self.get_color(bulb_name)
        hex_color = '#{:02X}{:02X}{:02X}'.format(color)
        return hex_color
    
    def get_colors(self) -> None:
        for bulb_thread in self.bulb_threads:
            i = self.find_bulb_index(bulb_thread.bulb_param['name'])
            if bulb_thread.new_state == 'off':
                self.bulb_colors[i] = (0, 0, 0)
            else:
                self.bulb_colors[i] = bulb_thread.new_color

    def get_color(self, 
            bulb_name: str) -> Tuple[int, int, int]:
        for bulb_thread in self.bulb_threads:
            if bulb_thread.bulb_param['name'] == bulb_name:
                return bulb_thread.get_color()
                # i = self.find_bulb_index(bulb_name)
                # self.bulb_colors[i] = bulb_thread.color
                # return self.bulb_colors[i]

    def rotate(self, 
            speed = 10, 
            update_pause = 0.5, 
            clockwise = True):
        self.bulb_rotation_colors = []
        # Make a copy of the original bulb_colors list to perform rotation.
        for bulb in self.bulb_params:
            bulbname = bulb['name']
            color = self.get_color(bulbname)                    
            self.bulb_rotation_colors.append(color)
        self.rotation_speed = float(speed)
        self.rotation_update_pause = float(update_pause)
        self.rotation_clockwise = clockwise        
        self.is_rotation_active = True
    
    def rotation_worker(self) -> None:
        cycle = 0
        while self.is_running:
            if self.is_rotation_active:
                if cycle == 0: 
                    cycle = self.rotation_speed
                    original_colors = self.bulb_rotation_colors.copy()
                cycle_percent = (self.rotation_speed - cycle) / self.rotation_speed
                cycle -= 1
                # Calculate the new colors and update the bulbs.
                for i in range(len(self.bulb_params)):
                    # Determine the neighboring color index based on the rotation direction.
                    if self.rotation_clockwise:
                        next_idx = (i + 1) % len(self.bulb_params)
                    else:
                        next_idx = (i - 1) % len(self.bulb_params)
                    # Interpolate the color
                    new_color = self.interpolate_color(original_colors[i], original_colors[next_idx], cycle_percent)
                    bulbname = self.bulb_params[i]['name']
                    self.set_color(bulbname, new_color)                    
                if cycle == 0: 
                     if self.rotation_clockwise:
                        first_item = self.bulb_rotation_colors[0]
                        self.bulb_rotation_colors = self.bulb_rotation_colors[1:] + [first_item]
                     else:
                        last_item = self.bulb_rotation_colors[-1]
                        self.bulb_rotation_colors = [last_item] + self.bulb_rotation_colors[:-1]
            time.sleep(self.rotation_update_pause)

    def get_names(self) -> str:
        return [entry['name'] for entry in self.bulb_params]

    def stop(self) -> None:
        self.is_rotation_active = False

    def get_colors_json_rgb_dump(self):
        compressed_data = {}
        for i, color in enumerate(self.bulb_colors):
            name = self.bulb_params[i]['name']
            compressed_data[name] = color
        return json.dumps(self.get_colors_json_rgb(), separators=(',', ':'))    
    
    def get_colors_json_rgb(self):
        compressed_data = {}
        for i, color in enumerate(self.bulb_colors):
            name = self.bulb_params[i]['name']
            compressed_data[name] = color
        return compressed_data
    
    def get_colors_json_hex_dump(self):
        return json.dumps(self.get_colors_json_hex(), separators=(',', ':'))    
    
    def get_colors_json_hex(self):
        compressed_data = {}
        for i, color in enumerate(self.bulb_colors):
            name = self.bulb_params[i]['name']
            hex_color = '#{:02X}{:02X}{:02X}'.format(*color)
            compressed_data[name] = hex_color
        return compressed_data

    def interpolate_color(self, 
            color1: Tuple[int, int, int], 
            color2: Tuple[int, int, int], 
            interpolationGrade: float) -> Tuple[int, int, int]:
        rDist = color2[0] - color1[0]
        gDist = color2[1] - color1[1]
        bDist = color2[2] - color1[2]
        r = int(color1[0] + interpolationGrade * rDist)
        g = int(color1[1] + interpolationGrade * gDist)
        b = int(color1[2] + interpolationGrade * bDist)
        return (r, g, b)            
    
    # convert hhhhssssvvvv hsv color string to (R, G, B)
    def hsv_string_to_rgb(self, 
            hsv: str) -> Tuple[int, int, int]:
        color_value = int(hsv[0:4], 16)
        saturation_value = int(hsv[4:8], 16)
        brightness_value = int(hsv[8:12], 16)
        # convert to values between 0 and 1
        color_value = color_value / 360.0
        saturation_value = saturation_value / 1000.0
        brightness_value = brightness_value / 1000.0
        # calculate rgb from hsv values
        hsv = (color_value, saturation_value, brightness_value)
        rgb = colorsys.hsv_to_rgb(*hsv)
        return (int(rgb[0] * 255), int(rgb[1] * 255), int(rgb[2] * 255))
    

class OutletDeviceThread(threading.Thread):
    def __init__(
            self, 
            outlet_param: Dict[str, str],
            connection_event: threading.Event, 
            status_event: threading.Event, 
            state_change_condition: threading.Condition) -> None:
        super().__init__()
        self.state_change_condition = state_change_condition
        self.outlet_param = outlet_param
        self.connection_event = connection_event
        self.status_event = status_event
        self.desired_state = False
        self.state = False
        self.error = False
        self.watt = 0
        self.is_running = True

    def shutdown(self) -> None:
        self.is_running = False

    def change_state(
            self,
            state: bool) -> None:

        with self.state_change_condition:
            self.desired_state = state
            self.state_change_condition.notify()

    def get_state(self) -> bool:
        self.check_state()
        return self.state

    def set_state(
            self,
            state: bool,
            nowait=True,
            sleep_time=0.1) -> None:
        log.low(f'  [house] trying to set {self.outlet_param["name"]} '
                f'state set to: {state}')
        if state:
            self.device.turn_on(nowait=nowait)
        else:
            self.device.turn_off(nowait=nowait)
        # log.low(f'  [house] {self.outlet_param["name"]} state result: {result}')
        # we don't ever dds the plug, so safest bet is forcewait after set state
        time.sleep(sleep_time)

    def check_state(self):
        data = self.device.status()
        if 'error' in data:
            self.error = True
            log.dbg(f"  [house] 🔌 error {self.outlet_param['name']}: {data}")
        else:
            self.error = False

        self.state = False
        self.watt = 0
        if 'dps' in data:
            dps = data['dps']
            self.state = dps['1']
            self.watt = dps['19']

    def run(self) -> None:
        name = self.outlet_param["name"]
        id = self.outlet_param['id']
        ip = self.outlet_param['ip']
        key = self.outlet_param['key']
        deviceVersion = float(self.outlet_param['version'])
        
        self.device = tinytuya.OutletDevice(id, ip, key)
        self.device.set_version(deviceVersion)
        self.connection_event.set()

        self.check_state()
        self.status_event.set()

        # Enter worker cycle
        while self.is_running:
            with self.state_change_condition:
                notified = self.state_change_condition.wait(timeout=4.9)
                if notified:
                    self.set_state(self.desired_state)
                time.sleep(0.1)
                self.check_state()

class OutletManager():
    def __init__(self, outlets) -> None:
        self.outlet_param = outlets
        self.is_running = True

        num_outlets = len(self.outlet_param)
        self.connection_events = [threading.Event() for _ in range(num_outlets)]
        self.status_events = [threading.Event() for _ in range(num_outlets)]
        state_change_conditions = [threading.Condition() for _ in range(num_outlets)]
        self.outlet_threads = [OutletDeviceThread(self.outlet_param[i], self.connection_events[i], self.status_events[i], state_change_conditions[i]) for i in range(num_outlets)]
        for thread in self.outlet_threads:
            thread.start()   

    def find_outlet_index(self, 
            name: str) -> int:
        for outlet in self.outlet_param:
            if outlet['name'].lower() == name.lower():
                return self.outlet_param.index(outlet)
        return -1

    def set_state(
            self,
            outlet_name: str,
            state: bool) -> None:
        i = self.find_outlet_index(outlet_name)
        outlet_names = [b["name"] for b in self.outlet_param]
        outlet_names_string = ', '.join(outlet_names)
        if i == -1:
            return {
                "result": "error",
                "reason": f"device name {outlet_name} not found, "
                f"must be one of these: {outlet_names_string}",
            }        
        for outlet_thread in self.outlet_threads:
            if outlet_thread.outlet_param['name'].lower() == outlet_name.lower():
                outlet_thread.change_state(state)
                wait_time = 3 # seconds
                current_time = time.time()
                end_time = current_time + wait_time
                while time.time() < end_time:
                    if outlet_thread.get_state() == state:
                        return {
                            "result": "success",
                            "state": state
                        }
                    time.sleep(0.2)
                return {
                    "result": "error",
                    "state": "could not set state in time",
                }
        return {
            "result": "error",
            "reason": f"device thread for device {outlet_name} not found",
        }

    def get_states(self, 
                outlet_names: List[str]) -> bool:
        results = []
        outlet_names = [b["name"] for b in self.outlet_param]
        outlet_names_string = ', '.join(outlet_names)
        for outlet_name in outlet_names:
            found = False
            for outlet_thread in self.outlet_threads:
                if outlet_thread.outlet_param['name'].lower() == outlet_name.lower():
                    i = self.find_outlet_index(outlet_name)
                    if i != -1:
                        results.append({
                            "result": "success",
                            "state" : outlet_thread.state,
                        })
            if not found:

                results.append({
                    "result": "error",
                    "reason" : f"outlet name {outlet_name} not found, must be one of these: {outlet_names_string}",
                })
        return results
    
    def get_state(self, outlet_name: str) -> bool:
        for outlet_thread in self.outlet_threads:
            if outlet_thread.outlet_param['name'].lower() == outlet_name.lower():
                return outlet_thread.state
        return False

    def get_power(self, outlet_name: str) -> int:
        for outlet_thread in self.outlet_threads:
            if outlet_thread.outlet_param['name'].lower() == outlet_name.lower():
                return outlet_thread.watt
        return 0

    def shutdown(self) -> None:
        log.low('  [house] shutting down smart plug threads')
        self.is_running = False
        for thread in self.outlet_threads:
            thread.shutdown()
        for thread in self.outlet_threads:
            thread.join()

    def wait_ready(self) -> None:
        # Wait for all bulbs connected
        log.low('  [house] connecting to outlets')
        for event in self.connection_events:
            event.wait()
        log.inf('  [house] outlets connected')
        # Wait for all bulbs status requested
        for event in self.status_events:
            event.wait()
        log.inf('  [house] outlets status check complete')
