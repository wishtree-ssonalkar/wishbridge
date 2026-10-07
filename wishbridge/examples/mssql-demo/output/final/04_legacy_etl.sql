-- Nightly customer value refresh (legacy ETL)
CREATE
    PROCEDURE main.wishbridge_demo.`usp_RefreshCustomerValue`()
    LANGUAGE SQL
    SQL SECURITY INVOKER
    AS
        BEGIN
            -- SET NOCOUNT ON - Databricks SQL does not return row count messages, equivalent to NOCOUNT ON
            
            CREATE
                TEMPORARY TABLE `#CustomerValue` AS
                SELECT o.`CustomerID`, SUM(o.`Amount`) AS LifetimeValue
                FROM main.wishbridge_demo.`Orders` AS o WHERE o.`Status` != 'Cancelled' GROUP BY o.`CustomerID`;
                -- FIXME: TSQL table hint has no Databricks equivalent and can affect semantics: NOLOCK
            MERGE INTO c
            USING main.wishbridge_demo.`Customers` AS c INNER JOIN `#CustomerValue` AS v ON v.CustomerID = c.CustomerID
            ON v.LifetimeValue > 1000
            WHEN MATCHED THEN UPDATE SET c.`Email` = LOWER(c.`Email`);
            -- FIXME: Databricks does not support PRINT: PRINT 'Rows updated: ' + CAST(@@ROWCOUNT AS VARCHAR(10))
            
            DROP TABLE `#CustomerValue`;
        END;

