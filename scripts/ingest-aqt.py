"""Ingests a number of days of CROCUS WXT data

Usage:
    python ./ingest-aqt.py ndays year month day site out_directory
    For example, for the Northwestern site on June 20, 2024, one would use, and output would be in the current directory
    python ingest-aqt.py 1 2024 6 20 NU .

Author:
    Scott Collis - 5.9.2024
    Max Grover - 8.19.2024
    Joe O'Brien - 10.4.2025 - refactored
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

def ingest_aqt(nstart, nstop, global_attrs, var_attrs):
    """
    Ingest from CROCUS AQTs using the Sage Data Client. 

    By Default, ingests a whole day of AQT data and saves it as a NetCDF to odir
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
    valsxr - xarray DataSet
        Ingested AQT environmental, PM and gasious conc data at 10s temporal 
            frequency, with global and variables attributes set. 
    """
    # Define the YYYY-MM-DDTHH:MM:SSZ format that Sage requires
    start = nstart.strftime('%Y-%m-%dT%H:%M:%SZ')
    end = nstop.strftime('%Y-%m-%dT%H:%M:%SZ')

    df_aq = sage_data_client.query(start=start,
                                   end=end, 
                                   filter={
                                        "plugin" : global_attrs['plugin'],
                                        "vsn" : global_attrs['WSN'],
                                        "sensor" : "vaisala-aqt530"
                                   }
    )
    # check to see if the data exists for this date
    if df_aq.empty == True:
        print(f"\n{global_attrs['site_ID']} AQT Data Not Found")
        return xr.Dataset()
    else:
        # Rename specific column names
        pm25 = df_aq[df_aq['name']=='aqt.particle.pm2.5'].copy()
        pm10 = df_aq[df_aq['name']=='aqt.particle.pm1']
        pm100 = df_aq[df_aq['name']=='aqt.particle.pm10']
        no = df_aq[df_aq['name']=='aqt.gas.no']
        o3 = df_aq[df_aq['name']=='aqt.gas.ozone']
        no2 = df_aq[df_aq['name']=='aqt.gas.no2']
        co = df_aq[df_aq['name']=='aqt.gas.co']
        aqtemp = df_aq[df_aq['name']=='aqt.env.temp']
        aqhum = df_aq[df_aq['name']=='aqt.env.humidity']
        aqpres = df_aq[df_aq['name']=='aqt.env.pressure']

        # Convert instrument timestamp to Pandas Datatime object
        pm25['time'] = pd.DatetimeIndex(pm25['timestamp'].values)

        # Remove all meta data descriptions besides the index
        aqvals = pm25.loc[:, pm25.columns.intersection(["time"])]

        # Add all parameter to the output dataframe
        aqvals['pm2.5'] = pm25.value.to_numpy().astype(float)
        aqvals['pm1.0'] = pm10.value.to_numpy().astype(float)
        aqvals['pm10.0'] = pm100.value.to_numpy().astype(float)

        aqvals['no'] = no.value.to_numpy().astype(float)
        aqvals['o3'] = o3.value.to_numpy().astype(float)
        aqvals['no2'] = no2.value.to_numpy().astype(float)
        aqvals['co'] = co.value.to_numpy().astype(float)
        aqvals['temperature'] =  aqtemp.value.to_numpy().astype(float)
        aqvals['humidity'] =  aqhum.value.to_numpy().astype(float)
        aqvals['pressure'] =  aqpres.value.to_numpy().astype(float)

        # calculate dewpoint from relative humidity
        dp = dewpoint_from_relative_humidity(aqvals.temperature.to_numpy() * units.degC, 
                                             aqvals.humidity.to_numpy() * units.percent
        )
        aqvals['dewpoint'] = dp

        # Define the index
        aqvals = aqvals.set_index("time")
        valsxr = xr.Dataset.from_dataframe(aqvals)
        valsxr = valsxr.sortby('time')

        # Assign the global attributes
        valsxr = valsxr.assign_attrs(global_attrs)
        # Assign the individual parameter attributes
        for varname in var_attrs.keys():
            valsxr[varname] = valsxr[varname].assign_attrs(var_attrs[varname])

        # ---------
        # Apply QC
        #----------
        # Check for aerosol water vapor uptake and mask out
        cond = (0 < valsxr.humidity) & (valsxr.humidity < 98)
        valsxr = valsxr.where(cond, drop=False)

        # Check for failed PM sensor (i.e. zero PM observations)
        cond2 = (0.5 < valsxr["pm2.5"]) & (0.5 < valsxr["pm10.0"]) & (0.5 < valsxr["pm1.0"])
        valsxr = valsxr.where(cond2, drop=False)

        # Ensure time is saved properly
        valsxr["time"] = pd.to_datetime(valsxr.time)

        return valsxr

def main(args, global_attrs, var_attrs):
    ## -- Generation Information for Cron output --
    print(f"\nCROCUS - {args.site} Node - AQT Data Ingest")

    ## -- Define the number of days to process
    print(f"\n Starting {args.site} AQT Ingest on: {args.start_date}")
    print(f"Finishing {args.site} AQT Ingest on: {args.end_date}")
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
        ds_out = ingest_aqt(nstart, nstop, global_attrs, var_attrs)
        # Define the output name
        end_fname = nstart.strftime('_%Y%m%d_%H%M%S.nc')
        start_fname = (args.outdir +
                       global_attrs['site_ID'].lower() +
                       "/" +
                       global_attrs['aqt_path'] +
                       'crocus-' + 
                       global_attrs['site_ID'] +
                       '-' + 
                       'aqt-' + 
                       global_attrs['datalevel']
        )
        fname = start_fname + end_fname
        # Check to make sure data was returned
        if ds_out.data_vars:
            print(f"\n{args.site} AQT Ingest - Writing {fname}")

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
                print(f"\n{args.site} AQT Ingest Write Failure on {args.start_date}")
        else:
            print(f"\n{args.site} AQT Ingest - No Data to Write for {fname}")
            
if __name__ == '__main__':

    WAGGLE_TIMEZONE = "UTC"
    LOCAL_TIMEZONE = "America/Chicago"

    # Site attributes
    aqt_global_NEIU = {'conventions': "CF 1.10",
                       'site_ID' : "NEIU",
                      'CAMS_tag' : "CMS-AQT-004",
                      'datastream' : "crocus_neiu_aqt_a1",
                      'datalevel' : "a1",
                       "plugin" : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                       'WSN' : 'W08D',
                      'latitude' : 41.9804526,
                      'longitude' : -87.7196038,
                       'elevation' : 13.25,
                       'aqt_path' : "neiu-aqt-a1/"}

    aqt_global_NU = {'conventions': "CF 1.10",
                      'WSN':'W099',
                      'site_ID' : "NU",
                      'CAMS_tag' : "CMS-AQT-003",
                      'datastream' : "crocus_nu_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 42.051469749,
                      'longitude' : -87.677667183,
                      'elevation' : 21.5,
                      'aqt_path' : "nu-aqt-a1/"}

    aqt_global_CSU = {'conventions': "CF 1.10",
                      'WSN':'W08E',
                       'site_ID' : "CSU",
                      'CAMS_tag' : "CMS-AQT-002",
                      'datastream' : "crocus_csu_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 41.71991216,
                      'longitude' : -87.612834722,
                      'elevation' : 0.0,
                      'aqt_path' : "csu-aqt-a1/"}

    aqt_global_ATMOS = {'conventions': "CF 1.10",
                        'WSN':'W0A4',
                        'site_ID' : "ATMOS",
                        'CAMS_tag' : "CMS-AQT-001",
                        'datastream' : "crocus_atmos_aqt_a1",
                        'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                        'datalevel' : "a1",
                        'latitude' : 41.7016264,
                        'longitude' : -87.9956515,
                        'elevation' : 0.0,
                        'aqt_path' : "atmos-aqt-a1/"}

    aqt_global_UIC = {'conventions': "CF 1.10",
                      'WSN':'W096',
                      'site_ID' : "UIC",
                      'CAMS_tag' : "CMS-AQT-",
                      'datastream' : "crocus_uic_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 41.869407936,
                      'longitude' : -87.645806251,
                      'elevation' : 0.0,
                      'aqt_path' : "uic-aqt-a1/"}

    aqt_global_CCICS = {'conventions': "CF 1.10",
                      'WSN':'W08B',
                      'site_ID' : "NEIU_CCICS",
                      'CAMS_tag' : "CMS-AQT-",
                      'datastream' : "crocus_neiu_ccics_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 41.823038311,
                      'longitude' : -87.609379028,
                      'elevation' : 30.34,
                      'aqt_path' : "neiu_ccics-aqt-a1/"}

    aqt_global_BIG = {'conventions': "CF 1.10",
                      'WSN':'W0A0',
                      'site_ID' : "BIG",
                      'CAMS_tag' : "CMS-AQT-14",
                      'datastream' : "crocus_big_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 41.77702369,
                      'longitude' : -87.609721059,
                      'elevation' : 3.52,
                      'aqt_path' : "big-aqt-a1/"}

    aqt_global_HUM = {'conventions': "CF 1.10",
                      'WSN':'W0A1',
                      'site_ID' : "HUM",
                      'CAMS_tag' : "CMS-AQT-017",
                      'datastream' : "crocus_hum_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 41.905513206,
                      'longitude' : -87.703525713,
                      'elevation' : 4.0,
                      'aqt_path' : "hum-aqt-a1/"}

    aqt_global_DOWN = {'conventions': "CF 1.10",
                      'WSN':'W09D',
                      'site_ID' : "DOWN",
                      'CAMS_tag' : "CMS-AQT-010",
                      'datastream' : "crocus_down_aqt_a1",
                      'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                      'datalevel' : "a1",
                      'latitude' : 41.701476659,
                      'longitude' : -87.9953044,
                      'elevation' : 4.5,
                      'aqt_path' : "down-aqt-a1/"}

    aqt_global_SHEDD = {'conventions': "CF 1.10",
                        'WSN':'W09E',
                        'site_ID' : "SHEDD",
                        'CAMS_tag' : "CMS-AQT-019",
                        'datastream' : "crocus_shedd_aqt_a1",
                        'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                        'datalevel' : "a1",
                        'latitude' : 41.867918965,
                        'longitude' : -87.613535027,
                        'elevation' : 14.41,
                        "aqt_path" : "shedd-aqt-a1/"}

    aqt_global_VLPK = {'conventions': "CF 1.10",
                       'WSN':'W095',
                       'site_ID' : "VLPK",
                       'CAMS_tag' : "CMS-AQT-008",
                       'datastream' : "crocus_vlpk_aqt_a1",
                       'plugin' : "registry.sagecontinuum.org/jrobrien/waggle-aqt:0.23.5.04",
                       'datalevel' : "a1",
                       'latitude' : 41.884884633,
                       'longitude' : -87.978717410,
                       'elevation' : 8.5,
                       "aqt_path" : "vlpk-aqt-a1/"}

    #put these in a dictionary for accessing
    global_sites = {'NU' : aqt_global_NU,
                    'CSU': aqt_global_CSU,
                    'NEIU' : aqt_global_NEIU,
                    'ATMOS': aqt_global_ATMOS,
                    'UIC': aqt_global_UIC,
                    'NEIU_CCICS': aqt_global_CCICS,
                    'BIG': aqt_global_BIG,
                    'HUM': aqt_global_HUM,
                    "DOWN": aqt_global_DOWN,
                    "SHEDD": aqt_global_SHEDD,
                    "VLPK": aqt_global_VLPK}

    #Variable attributes
    var_attrs_aqt = {'pm2.5' : {'standard_name' : 'mole_concentration_of_pm2p5_ambient_aerosol_particles_in_air',
                                'units' : 'ug/m^3'},
                    'pm10.0' : {'standard_name' : 'mole_concentration_of_pm10p0_ambient_aerosol_particles_in_air',
                                'units' : 'ug/m^3'},
                    'pm1.0' : {'standard_name' : 'mole_concentration_of_pm1p0_ambient_aerosol_particles_in_air',
                               'units' : 'ug/m^3'},
                    'no' : {'standard_name' : 'mole_fraction_of_nitrogen_monoxide_in_air',
                            'units' : 'Parts Per Million'},
                    'o3' : {'standard_name' : 'mole_fraction_of_ozone_in_air',
                            'units' : 'Parts Per Million'},
                    'co' : {'standard_name' : 'mole_fraction_of_carbon_monoxide_in_air',
                            'units' : 'Parts Per Million'},
                    'no2' : {'standard_name' : 'mole_fraction_of_nitrogen_dioxide_in_air',
                            'units' : 'Parts Per Million'},
                    'temperature': {'standard_name' : 'air_temperature',
                            'units' : 'celsius'},
                    'humidity': {'standard_name' : 'relative_humidity',
                            'units' : 'percent'},
                    'dewpoint': {'standard_name' : 'dew_point_temperature',
                            'units' : 'celsius'},
                    'pressure': {'standard_name' : 'air_pressure',
                            'units' : 'hPa'}}

    #Parsing the command line
    descript = ("Generation of a CROCUS AQT Ingested File (b-level dataset)")
    parser = argparse.ArgumentParser(description=descript,
                                             usage=(
            "python ingest-aqt.py --site ATMOS --outdir /user/dev/crocus/"
        )
    )

    parser.add_argument("--start_date",
                        type=str,
                        dest='start_date',
                        default=(datetime.now(ZoneInfo(WAGGLE_TIMEZONE)).date() -
                                 timedelta(days=1)),
                        help="[Default|-24hrs] Date to Start AQT Ingest in YYYY-MM-DD format"
    )

    parser.add_argument("--end_date",
                        type=str,
                        dest='end_date',
                        default=datetime.now(ZoneInfo(WAGGLE_TIMEZONE)).date(),
                        help="[Default|Current Date] Date to Complete AQT Ingest in YYYY-MM-DD format"
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
                        help='[Default|Current Directory] Directory to output AQT b-level files. Default is current working directory.')

    args = parser.parse_args()

    site_args = global_sites[args.site]

    main(args, site_args, var_attrs_aqt)
