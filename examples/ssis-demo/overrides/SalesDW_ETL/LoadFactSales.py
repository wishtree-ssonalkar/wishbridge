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
PackageName = 'LoadFactSales'





# COMMAND ----------

# Processing node Package\Delete Last 7 Days, type EXECUTE_SQL
# component nameDelete Last 7 Days
# input parameters :
# output parameters :
Package_Delete_Last_7_Days = f"""DELETE FROM workspace.wishbridge_ssis.FactDailySales WHERE OrderDate >= DATEADD(DAY, -7, CAST(current_timestamp() AS DATE));"""
spark.sql(Package_Delete_Last_7_Days)

# COMMAND ----------

# Processing node Package\Load Daily Sales\Order Lines Source, type SOURCE
# component nameOrder Lines Source

Package_Load_Daily_Sales_Order_Lines_Source = f"""SELECT o.OrderID, o.CustomerID, CAST(o.OrderDate AS DATE) AS OrderDate, ol.ProductID,
       ol.Quantity, ol.UnitPrice, ol.Quantity * ol.UnitPrice * (1 - ol.Discount) AS LineAmount
FROM workspace.wishbridge_ssis_src.Orders AS o
JOIN workspace.wishbridge_ssis_src.OrderLines AS ol ON ol.OrderID = o.OrderID
WHERE o.OrderDate >= DATEADD(DAY, -7, CAST(current_timestamp() AS DATE)) AND o.Status <> 'Cancelled'
;"""
Package_Load_Daily_Sales_Order_Lines_Source = spark.sql(Package_Load_Daily_Sales_Order_Lines_Source)
Package_Load_Daily_Sales_Order_Lines_Source.createOrReplaceTempView('Package_Load_Daily_Sales_Order_Lines_Source')

# COMMAND ----------

# Processing node Package\Load Daily Sales\Lookup Customer Key, type LOOKUP
# component nameLookup Customer Key

Package_Load_Daily_Sales_Lookup_Customer_Key = f"""SELECT Package_Load_Daily_Sales_Order_Lines_Source.Quantity AS Quantity,
Package_Load_Daily_Sales_Order_Lines_Source.CustomerID AS CustomerID,
Package_Load_Daily_Sales_Order_Lines_Source.UnitPrice AS UnitPrice,
Package_Load_Daily_Sales_Order_Lines_Source.OrderID AS OrderID,
Package_Load_Daily_Sales_Order_Lines_Source.OrderDate AS OrderDate,
Package_Load_Daily_Sales_Order_Lines_Source.LineAmount AS LineAmount,
Package_Load_Daily_Sales_Order_Lines_Source.ProductID AS ProductID,
LOOKUP_TABLE.CustomerKey AS CustomerKey
FROM Package_Load_Daily_Sales_Order_Lines_Source
INNER JOIN (SELECT CustomerKey, CustomerID FROM workspace.wishbridge_ssis.DimCustomer) AS LOOKUP_TABLE
 ON Package_Load_Daily_Sales_Order_Lines_Source.CustomerID = LOOKUP_TABLE.CustomerID
WHERE LOOKUP_TABLE.CustomerKey IS NOT NULL"""
Package_Load_Daily_Sales_Lookup_Customer_Key = spark.sql(Package_Load_Daily_Sales_Lookup_Customer_Key)
Package_Load_Daily_Sales_Lookup_Customer_Key.createOrReplaceTempView('Package_Load_Daily_Sales_Lookup_Customer_Key')

# COMMAND ----------

# Processing node Package\Load Daily Sales\Aggregate by Day and Customer, type AGGREGATOR
# component nameAggregate by Day and Customer

# Hand-fixed (WishBridge override): the converter left this Aggregate empty. Rebuilt from the SSIS component:
# group by OrderDate, CustomerKey; OrderCount = count distinct OrderID; TotalQuantity / TotalAmount = sums.
Package_Load_Daily_Sales_Aggregate_by_Day_and_Customer = f"""SELECT
OrderDate,
CustomerKey,
COUNT(DISTINCT OrderID) AS OrderCount,
SUM(Quantity) AS TotalQuantity,
SUM(LineAmount) AS TotalAmount
FROM
Package_Load_Daily_Sales_Lookup_Customer_Key
GROUP BY OrderDate, CustomerKey"""
Package_Load_Daily_Sales_Aggregate_by_Day_and_Customer = spark.sql(Package_Load_Daily_Sales_Aggregate_by_Day_and_Customer)
Package_Load_Daily_Sales_Aggregate_by_Day_and_Customer.createOrReplaceTempView('Package_Load_Daily_Sales_Aggregate_by_Day_and_Customer')

# COMMAND ----------

# Processing node Package\Load Daily Sales\FactDailySales, type TARGET
# component nameFactDailySales

Package_Load_Daily_Sales_FactDailySales = f"""INSERT INTO workspace.wishbridge_ssis.FactDailySales
 SELECT
OrderDate,
CustomerKey,
OrderCount,
TotalQuantity,
TotalAmount
FROM Package_Load_Daily_Sales_Aggregate_by_Day_and_Customer"""
spark.sql(Package_Load_Daily_Sales_FactDailySales)
