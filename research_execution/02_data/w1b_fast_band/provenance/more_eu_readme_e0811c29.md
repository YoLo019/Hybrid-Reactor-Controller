# OpenData
The open data come from monitoring of renewable energy sources. 

In the Wind Park Data directory there is an open dataset offered by ENGIE Laborelec. The 'wind.parquet' file contains data with a granularity of 2 seconds from an ENGIE wind turbine. The available tags are the wind speed (m/s, measured at the top of the wind turbine), the wind direction (0-360°, measured at the top of the wind turbine), the produced active power (kW), the pitch angle of the blades (degrees), the rotor speed (rpm), the wind turbine nacelle direction (0-360°) and the cosinus and sinus of both nacelle and wind direction. For all these tags a data point is available every 2 seconds. The dataset covers 10 days so it has 432.000 rows.


In the Solar Park directory there is an open dataset that was generated within the context of the MORE project. The dataset, 
licensed by Inaccess (by Power Factors) under a CC-BY-NC license, is provided “as is” for
research and analysis within the PV energy sector.
This dataset offers valuable insights into energy production, weather conditions, and grid
performance, facilitating comprehensive analysis and research endeavors within the energy
domain.

Basic Description:
The dataset provides the (normalized) output (production) of a PV plant for the period 2016-
2023 with 15 minutes granularity. The dataset consists of three CSV files, as follows:
File 1: Weather Data
- Column Descriptions:
- Column 1: Timestamp (date and time)
- Column 2: Ambient Temperature (in Celsius)
- Column 3: Average Irradiance (W*m^-2)
- Column 4: Module Temperature (in Celsius)
- Column 5: Average precipitation (mm/h)
- Column 6: Average relative humidity (%)
- Column 7: Average wind direction (degrees)
- Column 8: Average wind speed (m*s^-1)
File 2: Plant Active Power
- Column Descriptions:
- Column 1: Timestamp (date and time)
- Column 2: Normalised Power Output: Total amount of power produced by the plant (per unit)
File 3: Array Power
- Column Descriptions:
- Column 1: Timestamp (date and time)
- Column 2-15: Normalised Power Output: Amount of power produced by each array of the
plant (per unit)
For inquiries or assistance, please contact us at grigorios.kotsis@powerfactors.com
