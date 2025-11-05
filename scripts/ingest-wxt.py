"""
Ingests a number of days of CROCUS WXT data

Usage:
    python ./ingest-wxt.py ndays year month day site out_directory

Author:
    Scott Collis - 5.9.2024
    Max Grover - 8.19.2024
    Joe O'Brien - 10.3.2025 - refactored
"""

import os
import argparse
import pandas as pd
import xarray as xr
from datetime import datetime, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo
from metpy.calc import dewpoint_from_relative_humidity, wet_bulb_temperature
from metpy.units import units

import sage_data_client

def ingest_wxt(nstart, nstop, global_attrs, var_attrs):
    """
    Ingest from CROCUS WXTs using the Sage Data Client. 

    By Default, ingests a whole day of WXT data and saves it as a NetCDF to odir
    Users can configure start_ and end_dates to ingest more than 24 hrs at a time. 
    
    Parameters
    ----------
    nstart : datetime object
        Date to start ingest

    nstop : datetime object
        Date to end ingest

    global_attrs : dict
        Attributes that are specific to the site.
        
    var_attrs : dict
        Attributes that map variables in Beehive to
        CF complaint netCDF valiables.
        
    Returns
    -------
    vals10xr - xarray DataSet
        Ingested WXT temperature and wind speed data at 10s temporal 
            frequency, with global and variables attributes set. 
    
    """
    # Define the YYYY-MM-DDTHH:MM:SSZ format that Sage requires
    start = nstart.strftime('%Y-%m-%dT%H:%M:%SZ')
    end = nstop.strftime('%Y-%m-%dT%H:%M:%SZ')
    
    # query the sage_data_client, breaking up wind/temp measurements
    df_temp = sage_data_client.query(start=start,
                                     end=end, 
                                     filter={
                                        "name" : 'wxt.env.temp|wxt.env.humidity|wxt.env.pressure|wxt.rain.accumulation',
                                        "plugin" : global_attrs['plugin'],
                                        "vsn" : global_attrs['WSN'],
                                        "sensor" : "vaisala-wxt536"
                                    }
    )
    winds = sage_data_client.query(start=start,
                                   end=end, 
                                   filter={
                                        "name" : 'wxt.wind.speed|wxt.wind.direction',
                                        "plugin" : global_attrs['plugin'],
                                        "vsn" : global_attrs['WSN'],
                                        "sensor" : "vaisala-wxt536"
                                    }
    )

    # check to see if data exists for this date
    if df_temp.empty == True or winds.empty == True:
        print(f"\n{global_attrs['site_ID']} WXT Data Not Found")
        return xr.Dataset()
    else:
        hums = df_temp[df_temp['name']=='wxt.env.humidity']
        temps = df_temp[df_temp['name']=='wxt.env.temp'].copy()
        pres = df_temp[df_temp['name']=='wxt.env.pressure']
        rain = df_temp[df_temp['name']=='wxt.rain.accumulation']

        minsamps = min([len(hums), len(temps), len(pres), len(rain)])

        temps['time'] = pd.DatetimeIndex(temps['timestamp'].values)

        vals = temps.set_index('time')[0:minsamps]
        vals['temperature'] = vals.value.to_numpy()[0:minsamps]
        vals['humidity'] = hums.value.to_numpy()[0:minsamps]
        vals['pressure'] = pres.value.to_numpy()[0:minsamps]
        vals['rainfall'] = rain.value.to_numpy()[0:minsamps]

        direction = winds[winds['name']=='wxt.wind.direction']
        speed = winds[winds['name']=='wxt.wind.speed'].copy()

        minsamps = min([len(speed), len(direction)])

        speed['time'] = pd.DatetimeIndex(speed['timestamp'].values)
        windy = speed.set_index('time')[0:minsamps]
        windy['speed'] = windy.value.to_numpy()[0:minsamps]
        windy['direction'] = direction.value.to_numpy()[0:minsamps]

        winds10mean = windy.resample('10s').mean(numeric_only=True).ffill()
        winds10max = windy.resample('10s').max(numeric_only=True).ffill()
        dp = dewpoint_from_relative_humidity(vals.temperature.to_numpy() * units.degC,
                                             vals.humidity.to_numpy() * units.percent)

        vals['dewpoint'] = dp
        #ffil gets rid of nans due to empty resample periods
        vals10 = vals.resample('10s').mean(numeric_only=True).ffill()
        wb = wet_bulb_temperature(vals10.pressure.to_numpy() * units.hPa,
                                  vals10.temperature.to_numpy() * units.degC,
                                  vals10.dewpoint.to_numpy() * units.degC)

        vals10['wetbulb'] = wb
        vals10['wind_dir_10s'] = winds10mean['direction']
        vals10['wind_mean_10s'] = winds10mean['speed']
        vals10['wind_max_10s'] = winds10max['speed']
        _ = vals10.pop('value')

        ## -- Create the final xarray DataSet, apply attributes --
        vals10xr = xr.Dataset.from_dataframe(vals10)
        vals10xr = vals10xr.sortby('time')

        vals10xr = vals10xr.assign_attrs(global_attrs)

        for varname in var_attrs.keys():
            vals10xr[varname] = vals10xr[varname].assign_attrs(var_attrs[varname])

        return vals10xr

def main(args, global_attrs, var_attrs):

    ## -- Generation Information for Cron output --
    print(f"\nCROCUS - {args.site} Node - WXT Data Ingest")

    ## -- Define the number of days to process
    print(f"\n Starting {args.site} WXT Ingest on: {args.start_date}")
    print(f"Finishing {args.site} WXT Ingest on: {args.end_date}")
    # check to see if user defined start and end times
    if type(args.start_date) == str:
        args.start_date = datetime.strptime(args.start_date, "%Y-%m-%d")
    if type(args.end_date) == str:
        args.end_date = datetime.strptime(args.end_date, "%Y-%m-%d")
    ndays = (args.end_date - args.start_date).days

    # -- Process the WXT data into daily files --
    for i in range(ndays):
        # Iterate over the number of days entered and process
        #   every 24 hours.
        nstart = args.start_date + timedelta(days=i)
        nstop = args.start_date + timedelta(days=i+1)
        # call ingest-wxt
        ds_out = ingest_wxt(nstart, nstop, global_attrs, var_attrs)
        # Define the output name
        end_fname = nstart.strftime('_%Y%m%d_%H%M%S.nc')
        start_fname = (args.outdir +
                       global_attrs['site_ID'].lower() +
                       "/" +
                       global_attrs['wxt_path'] +
                       'crocus-' + 
                       global_attrs['site_ID'] +
                       '-' + 
                       'wxt-' + 
                       global_attrs['datalevel']
        )
        fname = start_fname + end_fname
        # Check to make sure data was returned
        if ds_out.data_vars:
            print(f"\n{args.site} WXT Ingest - Writing {fname}")

            # Check if directory exits
            Path(args.outdir + "/" + global_attrs['site_ID'].lower()).mkdir(exist_ok=True)

            # If file previously exists, remove it
            try:
                os.remove(fname)
            except OSError:
                pass

            # Write to file, utilizing distinct path per node
            try:
                ds_out.to_netcdf(fname)
            except ValueError as e:
                print(f"Error: {e}")
                print(f"Error type: {type(e).__name__}")
                print(f"\n{args.site} WXT Ingest Write Failure on {args.start_date}")
        else:
            print(f"\n{args.site} WXT Ingest - No Data to Write for {fname}")


if __name__ == '__main__':

    WAGGLE_TIMEZONE = "UTC"
    LOCAL_TIMEZONE = "America/Chicago"

    # Site attributes
    wxt_global_NEIU = {'conventions': "CF 1.10",
                       'site_ID' : "NEIU",
                       'CAMS_tag' : "CMS-WXT-002",
                       'datastream' : "CMS_wxt536_NEIU_a1",
                       'datalevel' : "a1",
                       'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                       'WSN' : 'W08D',
                       'latitude' : 41.9804526,
                       'longitude' : -87.7196038,
                       'elevation' : 13.25,
                       'wxt_path' : "neiu-wxt-a1/"}

    wxt_global_NU = {'conventions': "CF 1.10",
                     'WSN':'W099',
                     'site_ID' : "NU",
                     'CAMS_tag' : "CMS-WXT-005",
                     'datastream' : "CMS_wxt536_NU_a1",
                     'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                     'datalevel' : "a1",
                     'latitude' : 42.051469749,
                     'longitude' : -87.677667183,
                     'elevation' : 21.5,
                     'wxt_path' : "nu-wxt-a1/"}

    wxt_global_CSU = {'conventions': "CF 1.10",
                      'WSN':'W08E',
                      'site_ID' : "CSU",
                      'CAMS_tag' : "CMS-WXT-003",
                      'datastream' : "CMS_wxt536_CSU_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                      'datalevel' : "a1",
                      'latitude' : 41.71996846,
                      'longitude' : -87.612805717,
                      'elevation' : 0.0,
                      'wxt_path' : "csu-wxt-a1/"}

    wxt_global_ATMOS = {'conventions': "CF 1.10",
                        'WSN':'W0A4',
                        'site_ID' : "ATMOS",
                        'CAMS_tag' : "CMS-WXT-001",
                        'datastream' : "CMS_wxt536_ATMOS_a1",
                        'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                        'datalevel' : "a1",
                        'latitude' : 41.7016264,
                        'longitude' : -87.9956515,
                        'elevation' : 0.0,
                        'wxt_path' : "atmos-wxt-a1/"}

    wxt_global_UIC = {'conventions': "CF 1.10",
                      'WSN':'W096',
                      'site_ID' : "UIC",
                      'CAMS_tag' : "CMS-WXT-006",
                      'datastream' : "CMS_wxt536_UIC_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                      'datalevel' : "a1",
                      'latitude' : 41.869407936,
                      'longitude' : -87.645806251,
                      'elevation' : 0.0,
                      'wxt_path' : "uic-wxt-a1/"}

    wxt_global_CCICS = {'conventions': "CF 1.10",
                      'WSN':'W08B',
                      'site_ID' : "NEIU_CCICS",
                      'CAMS_tag' : "CMS-WXT-013",
                      'datastream' : "CMS_wxt536_NEIU_CCICS_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                      'datalevel' : "a1",
                      'latitude' : 41.823038311,
                      'longitude' : -87.609379028,
                      'elevation' : 30.34,
                      'wxt_path' : "neiu_ccics-wxt-a1/"}
    
    wxt_global_BIG = {'conventions': "CF 1.10",
                      'WSN':'W0A0',
                      'site_ID' : "BIG",
                      'CAMS_tag' : "CMS-WXT-016",
                      'datastream' : "CMS_wxt536_BIG_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                      'datalevel' : "a1",
                      'latitude' : 41.77702369,
                      'longitude' : -87.609721059,
                      'elevation' : 3.52,
                      'wxt_path' : "big-wxt-a1/"}

    wxt_global_HUM = {'conventions': "CF 1.10",
                      'WSN':'W0A1',
                      'site_ID' : "HUM",
                      'CAMS_tag' : "CMS-WXT-010",
                      'datastream' : "CMS_wxt536_HUM_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                      'datalevel' : "a1",
                      'latitude' : 41.905513206,
                      'longitude' : -87.703525713,
                      'elevation' : 4.0,
                      'wxt_path' : "hum-wxt-a1/"}
    
    wxt_global_DOWN = {'conventions': "CF 1.10",
                      'WSN':'W09D',
                      'site_ID' : "DOWN",
                      'CAMS_tag' : "CMS-WXT-008",
                      'datastream' : "CMS_wxt536_DOWN_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                      'datalevel' : "a1",
                      'latitude' : 41.701476659,
                      'longitude' : -87.9953044,
                      'elevation' : 4.5,
                      'wxt_path' : "down-wxt-a1/"}
    
    wxt_global_SHEDD = {'conventions': "CF 1.10",
                        'WSN':'W09E',
                        'site_ID' : "SHEDD",
                        'CAMS_tag' : "CMS-WXT-007",
                        'datastream' : "CMS_wxt536_SHEDD_a1",
                        'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                        'datalevel' : "a1",
                        'latitude' : 41.867918965,
                        'longitude' : -87.613535027,
                        'elevation' : 14.41,
                        "wxt_path" : "shedd-wxt-a1/"}
    
    wxt_global_VLPK = {'conventions': "CF 1.10",
                       'WSN':'W095',
                       'site_ID' : "VLPK",
                       'CAMS_tag' : "CMS-WXT-004",
                       'datastream' : "CMS_wxt536_VLPK_a1",
                       'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-wxt536:0.*",
                       'datalevel' : "a1",
                       'latitude' : 41.884884633,
                       'longitude' : -87.978717410,
                       'elevation' : 8.5,
                       "wxt_path" : "vlpk-wxt-a1/"}
    #put these in a dictionary for accessing

    global_sites = {'NU' : wxt_global_NU,
                    'CSU': wxt_global_CSU,
                    'NEIU' : wxt_global_NEIU,
                    'ATMOS': wxt_global_ATMOS,
                    'UIC': wxt_global_UIC,
                    'NEIU_CCICS': wxt_global_CCICS,
                    "BIG": wxt_global_BIG,
                    'HUM': wxt_global_HUM,
                    "DOWN": wxt_global_DOWN,
                    "SHEDD": wxt_global_SHEDD,
                    "VLPK": wxt_global_VLPK}

    #Variable attributes
    var_attrs_wxt = {'temperature': {'standard_name' : 'air_temperature',
                           'units' : 'Celsius'},
                    'humidity': {'standard_name' : 'relative_humidity',
                           'units' : 'percent'},
                    'dewpoint': {'standard_name' : 'dew_point_temperature',
                           'units' : 'Celsius'},
                    'pressure': {'standard_name' : 'air_pressure',
                           'units' : 'hPa'},
                    'wind_mean_10s': {'standard_name' : 'wind_speed',
                           'units' : 'meters per second'},
                    'wind_max_10s': {'standard_name' : 'wind_speed',
                           'units' : 'meters per second'},
                    'wind_dir_10s': {'standard_name' : 'wind_from_direction',
                           'units' : 'degrees'},
                    'rainfall': {'standard_name' : 'precipitation_amount',
                           'units' : 'milimeters'}}


    #Parsing the command line
    descript = ("Generation of a CROCUS WXT Ingested File (b-level dataset)")
    parser = argparse.ArgumentParser(description=descript,
                                             usage=(
            "python ingest-wxt.py --date 20250304 --outdir /user/dev/crocus/"
        ))

    parser.add_argument("--start_date",
                        type=str,
                        dest='start_date',
                        default=(datetime.now(ZoneInfo(WAGGLE_TIMEZONE)).date() -
                                 timedelta(days=1)),
                        help="[Default|-24hrs] Date to Start WXT Ingest in YYYY-MM-DD format"
    )

    parser.add_argument("--end_date",
                        type=str,
                        dest='end_date',
                        default=datetime.now(ZoneInfo(WAGGLE_TIMEZONE)).date(),
                        help="[Default|Current Date] Date to Complete WXT Ingest in YYYY-MM-DD format"
    )

    parser.add_argument('--site',
                        type=str,
                        dest="site",
                        default="NEIU",
                        help='[Default|NEIU] Specific CROCUS node to ingest data from'
    )

    parser.add_argument('--outdir',
                        type=str,
                        dest="outdir",
                        default="./",
                        help='[Default|Current Directory] Directory to output WXT b-level files. Default is current working directory.')

    args = parser.parse_args()

    site_args = global_sites[args.site]

    main(args, site_args, var_attrs_wxt)
