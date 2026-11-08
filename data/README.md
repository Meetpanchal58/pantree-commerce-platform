# The `data` folder

This folder is where Pantree keeps its generated practice data. The data files are not included
in GitHub; each person creates them on their own computer. This keeps the project download
smaller and lets the dataset be rebuilt with the same settings.

## Create the data for the first time

Open PowerShell in the main project folder and run:

```powershell
.\.venv\Scripts\python.exe generate_data.py --mode initialize
.\.venv\Scripts\python.exe validators\preflight_checks.py
```

The first command creates example products, customers, orders, browsing activity, stock records,
and supporting data. It also writes `metrics.json`, smaller files arranged by date in
`_partitioned`, and progress information in `_state`. The second command checks that the files
were generated consistently.

**Warning:** `initialize` replaces generated Parquet files and simulator progress in this folder.
Save a copy first if you need to keep an existing generated dataset. It does not create or reset
the live shop database; that is a separate step described in the
[main setup guide](../README.md#start-here-setup-and-use-pantree).

## Add more days later

To continue an existing dataset instead of starting again, use:

```powershell
.\.venv\Scripts\python.exe generate_data.py --mode increment
```

This catches the data up through today, keeping the earlier generated history. It requires a
dataset that was already created with `initialize`.

Optional settings such as the number of customers, number of products, random seed, and end date
are described in the main guide's
[data size settings](../README.md#change-the-dataset-size-optional).
