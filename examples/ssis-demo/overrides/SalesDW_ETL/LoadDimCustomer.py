# Databricks notebook source
from curses import meta
from logging import exception
import sys
import re
import socket
import getpass
import uuid
import pandas as pd
from datetime import datetime, timedelta
from pyspark.context import SparkContext
from pyspark.sql.functions import *

StartTime = datetime.now().strftime("%Y%m%d %H:%M:%S")
MachineName = socket.gethostname()
UserName = getpass.getuser()
ExecutionInstanceGUID = str(uuid.uuid4())
PackageName = 'LoadDimCustomer'





# COMMAND ----------

# Processing node Package\Truncate Staging Customer, type EXECUTE_SQL
# component nameTruncate Staging Customer
# input parameters :
# output parameters :
Package_Truncate_Staging_Customer = f"""TRUNCATE TABLE workspace.wishbridge_ssis.Customer;"""
spark.sql(Package_Truncate_Staging_Customer)

# COMMAND ----------

# Processing node Package\Stage Customers\Customer Source, type SOURCE
# component nameCustomer Source

Package_Stage_Customers_Customer_Source = f"""SELECT c.CustomerID, c.FirstName, c.LastName, c.Email, c.City, c.Country, c.ModifiedDate
FROM workspace.wishbridge_ssis_src.Customer AS c 
WHERE c.IsActive = true  -- hand-fixed (WishBridge override): BIT arrives as BOOLEAN
;"""
Package_Stage_Customers_Customer_Source = spark.sql(Package_Stage_Customers_Customer_Source)
Package_Stage_Customers_Customer_Source.createOrReplaceTempView('Package_Stage_Customers_Customer_Source')

# COMMAND ----------

# Processing node Package\Stage Customers\Add Derived Columns, type EXPRESSION
# component nameAdd Derived Columns

Package_Stage_Customers_Add_Derived_Columns = f"""SELECT FirstName AS FirstName,
LastName AS LastName,
City AS City,
current_timestamp() AS LoadDate,
Country AS Country,
Email AS Email,
TRIM(FirstName) || ' ' || TRIM(LastName) AS FullName,
LOWER(SUBSTRING(Email, instr(Email, '@') + 1, length(Email))) AS EmailDomain,
CustomerID AS CustomerID,
ModifiedDate AS ModifiedDate
FROM Package_Stage_Customers_Customer_Source"""
Package_Stage_Customers_Add_Derived_Columns = spark.sql(Package_Stage_Customers_Add_Derived_Columns)
Package_Stage_Customers_Add_Derived_Columns.createOrReplaceTempView('Package_Stage_Customers_Add_Derived_Columns')

# COMMAND ----------

# Processing node Package\Stage Customers\Staging Customer, type TARGET
# component nameStaging Customer

Package_Stage_Customers_Staging_Customer = f"""INSERT INTO workspace.wishbridge_ssis.Customer
 SELECT
CustomerID,
FullName,
Email,
EmailDomain,
City,
Country,
ModifiedDate,
LoadDate
FROM Package_Stage_Customers_Add_Derived_Columns"""
spark.sql(Package_Stage_Customers_Staging_Customer)

# COMMAND ----------

# Processing node Package\Merge DimCustomer, type EXECUTE_SQL
# component nameMerge DimCustomer
# input parameters :
# output parameters :
Package_Merge_DimCustomer = f"""MERGE INTO workspace.wishbridge_ssis.DimCustomer AS tgt
USING workspace.wishbridge_ssis.Customer AS src
ON tgt.CustomerID = src.CustomerID
WHEN MATCHED AND (tgt.FullName <> src.FullName OR tgt.Email <> src.Email OR tgt.City <> src.City OR tgt.Country <> src.Country) THEN UPDATE SET tgt.FullName = src.FullName, tgt.Email = src.Email, tgt.EmailDomain = src.EmailDomain,
tgt.City = src.City, tgt.Country = src.Country, tgt.UpdatedDate = current_timestamp()
WHEN NOT MATCHED BY TARGET THEN INSERT (CustomerID, FullName, Email, EmailDomain, City, Country, CreatedDate, UpdatedDate)
VALUES (src.CustomerID, src.FullName, src.Email, src.EmailDomain, src.City, src.Country, current_timestamp(), current_timestamp());"""
spark.sql(Package_Merge_DimCustomer)
