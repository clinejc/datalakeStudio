import streamlit as st
import pandas as pd
import duckdb
import plotly.express as px
import numpy as np
import time
import os
import psutil
import gc
import openai
from s3Index import s3Search
import sys
from PIL import Image


st.set_page_config(layout="wide")

logo = Image.open('images/logo.png')
st.image(logo, width=200)

if len(sys.argv) > 1:
    S3_BUCKET = sys.argv[1]
else:
    S3_BUCKET = None    
startTime = queryTime = int(round(time.time() * 1000))
endTime = 0

def askGpt(question):
    openai.organization = st.secrets["openai_organization"]
    openai.api_key = st.secrets["openai_api_key"]
    if (len(st.session_state.tables) > 0):
        questionForChatGPT = " You have the following tables:"
        for table in st.session_state.tables:
            questionForChatGPT += " " + st.session_state.tables[table]["tableDescriptionForGPT"]
    questionForChatGPT += ". The query I need is:" + question
    print("Sending question to GPT-3: " + questionForChatGPT)
    completion = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[
        {"role": "system", "content": """You are a SQL assistant, you only have to answer with SQL queries, no other text, only SQL.
        """},
        {"role": "user", "content": questionForChatGPT, "name": "DatalakeStudio"}
        ])
    print("GPT-3 response: " + completion.choices[0].message.content)
    return completion.choices[0].message.content

@st.cache_resource
def init():
    try:
        access_key = st.secrets["s3_access_key_id"]
        secret = st.secrets["s3_secret_access_key"]
        duckdb.query("INSTALL httpfs;LOAD httpfs;SET s3_region='eu-west-1';SET s3_access_key_id='" + access_key + "';SET s3_secret_access_key='" + secret +"'")
    except:
        duckdb.query("INSTALL httpfs;LOAD httpfs")
        print("No s3 credentials found")
init()

if 'totalTime' not in st.session_state:
    st.session_state.totalTime = 0
    st.session_state.lastQuery = 0
if 'tables' not in st.session_state:
    st.session_state.tables = dict()
if 'selectedTable' not in st.session_state:
    st.session_state.selectedTable = None
if 'df' not in st.session_state:
    st.session_state.df = None
if 'candidates' not in st.session_state:
    st.session_state.candidates = []
if 'fileName' not in st.session_state:
    st.session_state.fileName = "https://gist.githubusercontent.com/netj/8836201/raw/6f9306ad21398ea43cba4f7d537619d0e07d5ae3/iris.csv"
    
@st.cache_data
def convert_df(df):
    print("Exporting to CSV")
    return df.to_csv().encode('utf-8')

def s3SearchFile():
    if (st.session_state.s3SearchText and not st.session_state.s3SearchText.startswith('/') and not st.session_state.s3SearchText.startswith('http') and S3_BUCKET is not None):
        print("Searching S3")
        st.session_state.candidates = []
        s3Paths = s3Search(S3_BUCKET, st.session_state.s3SearchText)
        print("S3 paths: " + str(s3Paths) + " len:" + str(len(s3Paths)))
        if len(s3Paths) > 5:
            total = len(s3Paths)
            s3Paths = s3Paths[:5]
            s3Paths.append(f'... y {total - 5} mas')
        st.session_state.candidates = s3Paths
        return
    else:
        if (S3_BUCKET is None):
            print("No S3_BUCKET defined")


def loadTable(tableName, fileName):
    duckdb.query("DROP TABLE IF EXISTS "+ tableName )
    
    if (fileName.endswith(".csv")):
        duckdb.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM read_csv_auto('" + fileName + "', HEADER=TRUE, SAMPLE_SIZE=1000000))")
    elif (fileName.endswith(".parquet")):
        duckdb.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM read_parquet('" + fileName + "'))")
    elif (fileName.endswith(".json")):
        duckdb.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM read_json_auto('" + fileName + "', maximum_object_size=60000000))")

    # For chatGpt
    fields = duckdb.query("DESCRIBE "+ tableName).df()
    tableDescription = ""
    for field in fields.iterrows():
        tableDescription += "," + field[1]["column_name"] + " (" + field[1]["column_type"] + ")"
    tableDescriptionForGPT = "I have a table called '"+ tableName +"' with fields:" + tableDescription[1:]
    
    table = {"name": tableName, "path": fileName, "tableDescriptionForGPT": tableDescriptionForGPT}
    st.session_state.tables[tableName]=table
    if (st.session_state.selectedTable is None): 
        st.session_state.selectedTable = tableName

def showTableScan(tableName):
    if (tableName != "-"):
        print("Showing table scan for " + tableName)
        st.write("Table name: " + tableName)
        count = duckdb.query("SELECT count(*) as total FROM "+ tableName).df()
        st.write("Records: " + str(count["total"].iloc[0]))
        tableDf = duckdb.query("SELECT * FROM "+ tableName +" LIMIT 1000").df()
        c1,c2,c3 = st.columns([1, 3, 4])
        with c1:
            st.write("Schema")
            # Check this arrow warn
            st.write(tableDf.dtypes)
        with c2:
            st.write("Description")
            st.write(tableDf.describe())
        with c3:
            st.write("Sample data (1000)")
            st.write(tableDf.head(1000))
        if (st.button("Delete table '" + tableName + "' 🚫")):
            tableDf = None
            duckdb.query("DROP TABLE "+ tableName)
            del st.session_state.tables[tableName]
            st.experimental_rerun() 
################### Load data #################

fcol1,fcol2,fcol3 = st.columns([4, 2, 1])
with fcol1:
    st.session_state.fileName = st.text_input('Local file, folder, http link or find S3 file (pressing Enter) 👇', st.session_state.fileName,  key='s3SearchText', on_change=s3SearchFile)
with fcol2:
    tableName = st.text_input('Table name', 'iris', key='tableName')
with fcol3:
    if (st.button("Load 👈")):
        if (str(st.session_state.fileName).endswith("/")):
            files = os.listdir(st.session_state.fileName)
            for file in files:
                if (file.endswith(".csv") or file.endswith(".parquet") or file.endswith(".json")):
                    tableName = os.path.splitext(file)[0]
                    loadTable(tableName, st.session_state.fileName + str(file))
        else:
            loadTable(tableName, str(st.session_state.fileName))          
        
if (len(st.session_state.candidates) > 0):
    st.markdown("#### Select a S3 file:")
    for path in st.session_state.candidates:
        if st.button(path):
            st.session_state.fileName = path
            st.experimental_rerun() 
    if (st.button("Close S3 file list")):
        st.session_state.candidates = []
        st.experimental_rerun()

################### Loaded tables       ################

if (len(st.session_state.tables) > 0):
    with st.expander(label="**Tables** 📄", expanded=True):
        #st.markdown("#### Tables:")
        tableList = duckdb.query("SHOW TABLES").df()
        # Get elements of tableList as an String array
        tableListArray = tableList["name"].to_list()
        # Complete the array with empty strings to have 10 elements
        tableListArray = tableListArray + (10 - len(tableListArray)) * ["-"]
        tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs(tableListArray)
        print("################ len(tableListArray): " + str(len(tableListArray)) + " tableListArray: " + str(tableListArray))
        with tab1:showTableScan(tableListArray[0])
        with tab2:showTableScan(tableListArray[1])
        with tab3:showTableScan(tableListArray[2])
        with tab4:showTableScan(tableListArray[3])
        with tab5:showTableScan(tableListArray[4])
        with tab6:showTableScan(tableListArray[5])
        with tab7:showTableScan(tableListArray[6])
        with tab8:showTableScan(tableListArray[7])
        with tab9:showTableScan(tableListArray[8])
        with tab10:showTableScan(tableListArray[9])
        
    with st.expander("Query", expanded=True):
        col1,col2 = st.columns(2)
        with col1:
            query = st.text_area("Query SQL","""SELECT * FROM YOUR_TABLE""")
            if st.button("Run query 🚀"):
                st.session_state.df = duckdb.query(query).df()
                st.session_state.df.columns = st.session_state.df.columns.str.replace('.', '_')
                queryTime = int(round(time.time() * 1000))
        with col2:
            askChat = st.text_area("Ask ChatGPT", "How many records have each table?")
            if st.button("Suggest query 🤔"):
                r = askGpt(askChat)
                st.text_area("ChatGPT answer", r)

################### Time and resources #################

    c1, c2, c3 = st.columns(3)
    with c1:
        pid = os.getpid()
        process = psutil.Process(pid)
        memory_info = process.memory_info()
        st.metric("Memory", str(round(memory_info.rss/1024/1024, 1)) + " Mb")
        if (st.button("Run GC 🧹")):
            collected_objects = gc.collect()
            print("Cleaned:", collected_objects)
    with c2:
        if (st.session_state.totalTime != 0):
            st.metric("Time last query", str(st.session_state.lastQuery) + " ms")    
    with c3:
        if (st.session_state.totalTime != 0):
            st.metric("Total Time", str(st.session_state.totalTime) + " ms")  

################### Column analysis    #################
with st.expander("Analysis", expanded=True):
    if (st.session_state.df is not None):
        dfOriginal = st.session_state.df
        dfFiltered = dfOriginal
        col1,col2 = st.columns([1, 5])
        with col1:
            st.markdown("#### Schema")
            st.write(dfOriginal.dtypes)
            st.write("Records: " + str(len(dfOriginal)))
        with col2:
            st.markdown("#### Sample data")
            if (len(dfOriginal.columns) < 10):
                st.write(dfOriginal.head(10))
            else:
                st.write(dfOriginal.head(len(dfOriginal.columns)))

            if (st.button("Download")):
                st.write("Download table")
                file_type = st.radio("Doanload as:", ("CSV", "Excel"), horizontal=True, label_visibility="collapsed")
                if file_type == "CSV":
                    file = convert_df(dfOriginal)
                    st.download_button("Download dataframe", file, "report.csv", "text/csv", use_container_width=True)
                elif file_type == "Excel":
                    file = convert_excel(dfOriginal)
                    st.download_button("Download dataframe", file, "report.xlsx", use_container_width=True)
        
        if (("lat" in dfOriginal.columns and "lon" in dfOriginal.columns) or
            ("latitude" in dfOriginal.columns and "longitude" in dfOriginal.columns)):

            st.header("Detected spatial data")
            st.map(dfOriginal)
        else:
            st.header("No spatial data detected")
            st.write("Spatial fields should be named 'lat', 'latitude', 'LAT', 'LATITUDE' AND 'lon', 'longitude', 'LON', 'LONGITUDE' to be plotted in a map, use a SQL query to rename them if needed: Ej: Latitude as lat, Longitude as lon")

        st.header("Column data analysis")
        for col in dfOriginal.columns:
            if (col.startswith("grp_")):
                continue
            st.divider()
            st.markdown("####  " + col)
            
            groupByValue = duckdb.query("SELECT " + col + ", count(*) as quantity FROM dfFiltered GROUP BY " + col + " ORDER BY quantity DESC").df()
            distinctValues = len(groupByValue)
            rcol1,rcol2 = st.columns([4, 2])
            if dfOriginal[col].dtype == 'object' or dfOriginal[col].dtype == 'bool':
                with rcol1:
                    if distinctValues < 100:
                        fig = px.pie(groupByValue, values='quantity', names=col, title=f"{col} Pie Chart")
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.write("Too many values ("+str(distinctValues)+") in "+col+" to plot a chart")
                    
                with rcol2:  
                    st.write(dfFiltered[col].describe())
            elif str(dfOriginal[col].dtype).startswith('datetime'):
                with rcol1:
                    st.write("Datetime column has no plots yet")
                with rcol2:
                    st.write(dfFiltered[col].describe())
            else:
                if (dfFiltered[col].describe()["std"] == 0):
                    st.write("Column "+col+" has always the same value: " + str(dfFiltered[col].iloc[0]))
                else:
                    with rcol1:
                        if (distinctValues < 500):
                            fig = px.bar(groupByValue, x=col, y='quantity', title=f"{col} Bar Chart")
                            st.plotly_chart(fig, use_container_width=True)
                        else:
                            num_intervals = 100
                            q5 = dfFiltered[col].quantile(0.05)
                            q95 = dfFiltered[col].quantile(0.95)
                            if dfOriginal[col].dtype == 'int64':
                                bins = np.arange(q5, q95 + 2, step=max(1, (q95 - q5 + 1) // num_intervals))
                                labels = [f"{i}-{(i + bins[1] - bins[0] - 1)}" for i in bins[:-1]]
                            else:                                
                                bins = np.linspace(q5, q95, num_intervals + 1)
                                labels = [f"{i:.4f}-{(i + (q95 - q5) / num_intervals):.4f}" for i in bins[:-1]]
                            if len(set(labels)) != len(labels):
                                print("labels:" + str(labels))
                                raise ValueError("Labels are not unique")
                            
                            dfFiltered['grp_'+col] = pd.cut(dfFiltered[col], bins=bins, labels=labels)
                            new_df = dfFiltered.groupby('grp_' + col).size().reset_index(name='quantity')
                            fig = px.line(new_df, x="grp_" + col, y="quantity", title=col + " Distribution")
                            st.plotly_chart(fig, use_container_width=True)
                    with rcol2:  
                        st.write(dfFiltered[col].describe())
                        st.write("Distinct values:" + str(distinctValues))

        endTime = int(round(time.time() * 1000))
        st.write("Query execution time: " + str(queryTime - startTime) + " ms")
        st.session_state.lastQuery = queryTime - startTime
        st.write("Total execution time: " + str(endTime - startTime) + " ms")
        st.session_state.totalTime = endTime - startTime

