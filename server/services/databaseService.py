import duckdb
import os
import logging as log
from zipfile import ZipFile
from pathlib import Path
from hashlib import sha256

import pyarrow as pa

import ujson

configLoaded = False
db = None

BUNDLE_DIR = Path(".mosaic/bundle")


format = "%(asctime)s %(filename)s:%(lineno)d - %(message)s "
log.basicConfig(format=format, level=log.INFO, datefmt="%H:%M:%S")
secretsLoaded = None

def init(secrets, config):
    global db
    global secretsLoaded
    secretsLoaded = secrets

    if not os.path.exists(config["downloadFolder"]):
        os.makedirs(config["downloadFolder"])
        print("Created folder " + config["downloadFolder"])
    #Check if config["databasesFolder"] folder exists. If not create it
    if not os.path.exists(config["databasesFolder"]):
        os.makedirs(config["databasesFolder"])
        print("Created folder " + config["databasesFolder"])


    if (config["databasesFolder"] is not None and config["defaultDatabase"] is not None):
        print("Connecting to database..." + config["defaultDatabase"])
        db = duckdb.connect(config["databasesFolder"] + "/" + config["defaultDatabase"], config={"allow_unsigned_extensions": "true"})
    else:
        print("Connecting to in-memory database")
        db = duckdb.connect(':memory:', config={"allow_unsigned_extensions": "true"})

    loadExtensions(secrets)

    global configLoaded
    configLoaded = True


def loadExtensions(secrets):
    try:
        runQuery("INSTALL httpfs;LOAD httpfs;SET s3_region='eu-west-1';")
        runQuery("INSTALL spatial;LOAD spatial;")
        runQuery("SET s3_access_key_id='" + secrets["s3_access_key_id"] + "';SET s3_secret_access_key='" + secrets[
            "s3_secret_access_key"] + "'", False)
        print("Loaded S3 credentials")
    except Exception as e:
        print("Could not load S3 credentials from secrets.yml file")
        runQuery("INSTALL httpfs;LOAD httpfs")
        runQuery("INSTALL spatial;LOAD spatial;")
        runQuery("INSTALL aws;LOAD aws")
        runQuery("CALL load_aws_credentials();")
    try:
        runQuery("INSTALL h3 FROM community;LOAD h3;")
        print("Loaded H3 extension")
    except Exception as e:
        print("Could not load H3 extension:  " + str(e))


####################################################
def loadTable(config, tableName, fileName):
    global configLoaded

    format_list = ['csv','tsv','parquet', 'gz', 'json', 'geojson', 'gpkg', 'kml', 'shp']
    if (configLoaded == False):
        print("Load config")
        return None
    data_dir = config["downloadFolder"]
    print("Loading table " + tableName + " from " + fileName)
    db.query("DROP TABLE IF EXISTS "+ tableName )

    extracted_files = []
    if fileName.endswith('.zip'):
        extracted_data_file = None
        with ZipFile(fileName, 'r') as zip:
            for info in zip.infolist():
                zip.extract(info, data_dir)
                extracted_files.append(os.path.join(data_dir, info.filename))
                if '.' in info.filename and info.filename.split('.')[-1] in format_list:
                    extracted_data_file = info.filename

        # original zip file removal
        os.remove(fileName)
        if extracted_data_file:
            fileName = os.path.join(data_dir, extracted_data_file)
    print('File to be integrated : ', fileName)
    if fileName.lower().endswith(".csv") or fileName.lower().endswith(".tsv"):
        try:
            db.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM read_csv_auto('" + fileName + "', HEADER=TRUE, SAMPLE_SIZE=1000000))")
        except Exception as e:
            print("####### Error reading CSV file: " + str(e))
    elif fileName.endswith(".parquet") or fileName.lower().endswith(".pq.gz"):
        db.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM read_parquet('" + fileName + "'))")
    elif fileName.lower().endswith(".json"):
        db.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM read_json_auto('" + fileName + "', maximum_object_size=60000000))")
    elif '.' in fileName and fileName.lower().split('.')[1] in ['shp','geojson','gpkg','kml']:
        db.query("INSTALL spatial;LOAD spatial;CREATE TABLE "+ tableName +" AS (SELECT * FROM ST_Read('" + fileName + "'))")

    if (not fileName.lower().startswith("s3") and not fileName.lower().startswith("http")):
        print("Removing file " + fileName)
        os.remove(fileName)
        # zip file content removal
        for f in extracted_files:
            try:
               os.remove(f)
            except:
                pass

    r = db.query('SHOW TABLES')
    if tableName in r.df()["name"].to_list():
        r.show()
        return True
    else:
        print("duckDbService: table " + tableName + " not loaded:" + str(r))
        return False

####################################################
def runQuery(query, logQuery=True, format = "df"):
    try:
        if (logQuery):
            print("Executing query: " + str(query))
        #else:
        #    print("Executing query XXXXXXX")


        r = db.query(query)
        if (r is not None):
            if (format == "arrow"):
                return r.arrow()
            else:
                return r.df()
    except Exception as e:
        if (logQuery):
            print("Error running query: " + str(e))
        else:
            print("Error running query XXXXXXX")
        # Raise exception to be handled by caller
        raise e
####################################################
def getTableList(hideMeta: bool = True):
    tableList = runQuery("SHOW TABLES")
    tableListArray = None
    if (tableList is not None):
        if (hideMeta):
            # Remove __lastQuery table form the list
            tableList = tableList[tableList["name"] != "__lastQuery"]
            tableList = tableList[tableList["name"] != "__queries"]
        tableListArray = tableList["name"].to_list()
    return tableListArray
####################################################
def getTableDescriptionForChatGpt(tableName):
    fields = db.query("DESCRIBE "+ tableName).df()
    tableDescription = ""
    for field in fields.iterrows():
        tableDescription += "," + field[1]["column_name"] + " (" + field[1]["column_type"] + ")"
    tableDescriptionForGPT = "One of the tables is called '"+ tableName +"' and has following fields:" + tableDescription[1:]
    return tableDescriptionForGPT
####################################################
def createTableFromDataFrame(df, tableName):
    print("Creating table " + tableName)
    db.query("DROP TABLE IF EXISTS "+ tableName )
    db.query("CREATE TABLE "+ tableName +" AS (SELECT * FROM "+ df +")")
####################################################

def exportData(tableName, format, fileName):
    if (format == "csv"):
        runQuery("COPY (SELECT * FROM "+ tableName +") TO '"+ fileName +"' (FORMAT CSV, HEADER)")
        return True
    elif (format == "parquet"):
        runQuery("COPY (SELECT * FROM "+ tableName +") TO '"+ fileName +"' (FORMAT PARQUET)")
        return True
    else:
        print("Format not supported")
        return False

####################################################

def getProfile(tableName):

    query = "SELECT 'count' AS statistic"
    fields = db.query("DESCRIBE "+ tableName).df()
    print("fields:"  + str(fields))

    # Only BIGINT and DOUBLE
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",COUNT(" + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'mean' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",AVG(" + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'std' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",STDDEV(" + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'min' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",MIN(" + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'p25' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY " + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'p50' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY " + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'p75' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY " + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName
    query += " UNION ALL SELECT 'max' AS statistic"
    for field in fields.iterrows():
        if field[1]["column_type"] in ["BIGINT", "DOUBLE"]:
            query += ",MAX(" + field[1]["column_name"] + ") AS " + field[1]["column_name"]
    query += " FROM " + tableName

    print(query)
    return runQuery(query)

####################################################

def getDatabaseList(config):
    # Return list of database files (*.db) in config["databasesFolder"]
    files = os.listdir(config["databasesFolder"])
    dbFiles = []
    for file in files:
        if file.endswith(".db"):
            # Remove file extension .db
            file = file[:-3]
            dbFiles.append(file)
    return dbFiles


def changeDatabase(config, databaseName):
    global db
    log.info("Changing database to " + databaseName)
    db.close()
    db = duckdb.connect(config["databasesFolder"] + "/" + databaseName + ".db")
    # Load extensions
    loadExtensions(secretsLoaded)

    return True

def createDatabase(config, databaseName):
    log.info("Creating database " + databaseName)
    duckdb.connect(config["databasesFolder"] + "/" + databaseName)
    return True

############################################
def retrieve_arrow_bytes(query):
    sql = query.get("sql")
    result = get_arrow_bytes(sql)
    return result

def get_arrow(sql):
    result = runQuery(sql, True, "arrow")
    return result

def arrow_to_bytes(arrow):
    sink = pa.BufferOutputStream()
    with pa.ipc.new_stream(sink, arrow.schema) as writer:
        writer.write(arrow)
    return sink.getvalue().to_pybytes()

def get_arrow_bytes(sql):
    return arrow_to_bytes(get_arrow(sql))
