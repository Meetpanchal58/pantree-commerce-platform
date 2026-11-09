# Glossary

## A few words you may see

| Word | In everyday language |
|---|---|
| Dataset / warehouse | The collection of generated tables about customers, products, orders, and activity. |
| Parquet | A compact file format used for the generated tables in `data`. |
| SQLite | The local database that the live shop reads and updates. |
| DuckDB | A separate local database used to explore and analyze a copy of selected shop data. |
| OTP | A one-time sign-in code sent to your email or phone. |
| Cold start | A shopper or product with too little history to personalize recommendations yet. |
| Candidate | A product considered as a possible recommendation before filtering and sorting. |
| SMTP | The standard way an app connects to an email service to send messages. |
| SuperTokens | The sign-in service that creates/verifies codes and remembers sign-in sessions. |
| FastAPI | The Python web framework used to run the shop. You do not need to use it directly. |
| OLTP | A technical name for the database that handles everyday shop activity. |
| OLAP | A technical name for the separate copy used to study or report on activity. |
| ALS | A way to find products based on patterns in what shoppers view, add, or buy. |
| FAISS | A tool that quickly finds products with descriptions/details similar to another product. |

For a deeper explanation of the generator, simulator settings, and output files, see
[Data generation and refresh](running.md#refreshing-data-over-time-incremental) and
[the data folder guide](../data/README.md). For deployment on a public server, use the
[production setup](deployment.md#production).
