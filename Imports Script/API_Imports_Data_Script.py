import pandas as pd
import pickle as pkl
import yaml
import numpy as np
import requests
from pathlib import Path

apiPath = Path(__file__).parent / 'API'
dataPath = Path(__file__).parent / 'Data'
conPath = Path(__file__).parent / 'Concordances'
  
request_data = False #0 for no, 1 for yes
    
#%%

def get_URL_Components(file):
    '''
    Loads yaml file for corresponding data source (BEA or Census). Yaml files
    contain most (excluding country and year information) structures necessary
    to make requests to either Census or BEA API. Returns yaml-loaded
    dictionary. 
    '''
    with open(apiPath / file) as f:
        try:
            print('Successfully Loaded',file[:-8],'URL Components')
            m = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            print(exc)
    return m

def get_CTY_CODE(file='country.txt'):
    '''
    Pulls in txt file of countries from Census to extract country codes 
    necessary to make requests in Census API. Returns dataframe of country:
    code items.
    '''
    l = []
    with open(conPath / file) as f:
        for line in f:
            a = line.split('|')
            l2 = []
            for item in a:
                l2.append(item.strip())
            if len(l2)>=3:
                l.append(l2)
    headers = l[0]
    df = pd.DataFrame(l, columns=headers)
    df = df.iloc[1:,:]
    df = df.rename(columns={'Code':'Census Code'})
    return(df)

def get_country_schema():
    '''
    Uses t_e dataframe, containing a concordance between countries across
    exiobase, BEA TiVA regions, BEA Service Imports, and Census Codes (not 
    used). The function creates three dataframes 1) b_d is a concordance 
    between exiobase ISO country codes and BEA service imports countries 
    (strings with their API name equivalents); and 2) c_d is a concordance 
    between exiobase ISO codes and Census country codes (4-digit)
    '''
    cty=get_CTY_CODE()
    t_e = pd.read_csv(conPath / 'exio_tiva_concordance.csv')
    df = t_e.rename(columns={'ISO 3166-alpha-2':'ISO Code', 
                             'BEA_AREAORCOUNTRY':'BEA'})
    b_c = df[['ISO Code','BEA']].dropna(axis='index',how='any')
    b_d = b_c.set_index('ISO Code')['BEA'].to_dict()

    c_c = df[['ISO Code']].dropna(axis='index',how='any')
    c_c = (pd.merge(c_c, cty, how='left',on='ISO Code')
           .drop(columns='Name')
           .dropna(axis='index', how='any'))
    c_d = c_c.set_index('ISO Code')['Census Code'].to_dict()
    return (b_d, c_d)

def create_Reqs(file,d):
    '''
    A function to develop all requests to either Census or BEA API. Requests 
    are developed and stored in a dictionary of the following structure:
    reqs = {year:{year_country:{year:YYYY, country=country, req: url}}}
    '''
    components = get_URL_Components(file)
    reqs = {}
    for year in components['years']:
        year_reqs = {}
        year = str(year)
        comp = components['url']
        req_url = comp['base_url']
        try: 
            a = comp['api_path']
            req_url += a
            ## TODO update handling of API key
        except KeyError:
            pass
        for key, value in comp['url_params'].items():
            string = f'{key}={value}&'
            req_url += string
        req_url = req_url.rstrip('&')
        year_reqs = complete_URLs(req_url, year, d)
        reqs[year]=year_reqs
    print('Successfully Created All', file[:-8], 'Request URLs')
    return reqs

def complete_URLs(req_url, year, d):
    '''
    A function to replace the __areaorcountry__ and __year__ components of the
    requests with the country and year of the request, respectively.
    '''
    ctys = [value for key, value in d.items() if value != '1000']
    l = {}
    for cty in ctys:
        try:
            cty = str(cty)
        except ValueError:
            pass
        key = year+'_'+cty
        l[key]={}
        full_req = (req_url
                    .replace('__areaorcountry__', cty)
                    .replace('__year__', year))
        l[key]['year'] = year
        l[key]['cty'] = cty
        l[key]['req'] = full_req
    year_reqs = l
    return year_reqs

def make_reqs(file, reqs, data_years):
    '''
    A function to make requests to either the BEA or Census API. Stores all
    responses in a dictionary of the following format:
    d = {year:{year:YYYY, cty:cty, req_url:req_url, data:response}}
    '''
    d={}
    for year in data_years:
        year_reqs = reqs[year]
        d[year] = {}
        for key, value in year_reqs.items():
            response = requests.get(value['req'])
            value['data'] = response.json()
            d[year][key] = value
    print('Successfully Collected All',file,'Requests')
    return d

def get_census_df(d, c_d, data_years):
    '''
    Creates a dataframe for Census response data for a given year.
    '''
    df = pd.DataFrame()
    country_code = {v:k for k,v in c_d.items()}
    for year in data_years:
        for k, v in d[year].items():
            v_d = v['data']
            cty = country_code.get(v['cty'])
            value_df = pd.DataFrame(data=v_d[1:], columns=v_d[0])
            cols = value_df[['NAICS','GEN_CIF_YR']]
            cols = (cols
                    .assign(GEN_CIF_YR = lambda x: (x['GEN_CIF_YR']
                                                    .astype(float)
                                                    ))
                    .rename(columns={'GEN_CIF_YR':cty})
                    .set_index('NAICS')
                    )
            df = pd.concat([df, cols], axis=1)
        df = df.assign(Year=year)
    df = df.replace(np.nan, 0).reset_index()
    ## Merge in BEA Codes and flatten
    c_b = pd.read_csv(apiPath / 'Census_API_Mappings.csv')
    df = df.merge(c_b, how='left', on='NAICS')
    df = (df.drop(columns='NAICS')
            .groupby(['BEA Sector', 'Year']).agg(sum)
            .reset_index()
            .melt(id_vars=['BEA Sector', 'Year'], var_name='CountryCode',
                  value_name='Import Quantity')
            .assign(Unit='USD')
            .assign(Source='Census')
            )
    return df

def get_bea_df(d, b_d, data_years):
    '''
    Creates a dataframe for BEA response data for a given year.
    '''
    e_t_d = {v:k for k,v in b_d.items()}
    n_d = {}
    df_all = pd.DataFrame()
    b_b = (pd.read_csv(apiPath / 'BEA_API_Mappings.csv')
           .filter(['API BEA Service', 'BEA Sector'])
           .rename(columns={'API BEA Service': 'BEA Service'})
           )
    for year in data_years:
        for k, v in d[year].items():
            cty = v['cty']
            cty = e_t_d[cty]
            d_n = {}
            data = v['data']['BEAAPI']['Results']['Data']
            for item in data:
                sector = item['TypeOfService']
                value = item['DataValue']
                d_n[sector] = value
            n_d[cty] = d_n
        df = (pd.DataFrame(n_d)
              .apply(pd.to_numeric)
              .dropna(how='all')
              .replace(np.nan,0)
              .reset_index()
              .rename(columns={'index':'BEA Service'})
              )
        ## Merge in BEA codes and flatten
        df = (df.merge(b_b, how='right', on='BEA Service', validate='1:m')
              .fillna(0)
              .drop(columns='BEA Service')
              )
        if(len(df['BEA Sector'].unique()) != len(df)):
            raise ValueError("Duplicate BEA sectors")
        df = (df.melt(id_vars=['BEA Sector'],
                      var_name='CountryCode',
                      value_name='Import Quantity')
                .assign(Unit='USD')
                .assign(Source='BEA')
                .assign(Year=year)
                )
        df['Import Quantity'] = df['Import Quantity'].apply(lambda x: x*1000000)
        df_all = pd.concat([df_all, df], ignore_index=True)
    return df_all

def get_imports_data(request_data, year=2020):
    '''
    A function to call from other scripts.
    '''
    b_d, c_d = get_country_schema()
    year = str(year)
    if request_data == True:    
        b_reqs = create_Reqs('BEA_API.yml', b_d)
        c_reqs = create_Reqs('Census_API.yml', c_d)
        b_resp = make_reqs('BEA', b_reqs, [year])
        pkl.dump(b_resp, open(dataPath / f'bea_responses_{year}.pkl', 'wb'))
        c_resp = make_reqs('Census', c_reqs, [year])
        pkl.dump(c_resp, open(dataPath / f'census_responses{year}.pkl', 'wb'))

    c_responses = pkl.load(open(dataPath / f'census_responses_{year}.pkl', 'rb'))
    b_responses = pkl.load(open(dataPath / f'bea_responses_{year}.pkl', 'rb'))
    b_df = get_bea_df(b_responses, b_d, [year])
    c_df = get_census_df(c_responses, c_d, [year])
    i_df = pd.concat([c_df, b_df], ignore_index=True, axis=0)
    i_df['Country'] = i_df['CountryCode'].map(b_d)
    return(i_df)

if __name__ == '__main__':
    id_f = get_imports_data(request_data=request_data)
