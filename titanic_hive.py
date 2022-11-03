import os
import datetime as dt
import pandas as pd
from airflow.models import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
from airflow.providers.apache.hive.operators.hive import HiveOperator
from airflow.providers.telegram.operators.telegram import TelegramOperator
from airflow.utils.task_group import TaskGroup

default_args = {
    'owner': 'gb',
    'start_date': dt.datetime(2022, 5, 14),
    'retries': 1,
    'retry_delay': dt.timedelta(minutes=1)
}


def get_path(file_name):
    return os.path.join(os.path.expanduser('~'), file_name)


def download_titanic_dataset():
    url = 'https://web.stanford.edu/class/archive/cs/cs109/cs109.1166/stuff/titanic.csv'
    df = pd.read_csv(url)
    df.to_csv(get_path('titanic.csv'), encoding='utf-8')


def pivot_dataset():
    titanic_df = pd.read_csv(get_path('titanic.csv'))
    df = titanic_df.pivot_table(index=['Sex'],
                                columns=['Pclass'],
                                values='Name',
                                aggfunc='count').reset_index()
    df.to_csv(get_path('titanic_pivot.csv'))


# Написать функцию mean_fare_per_class(), которая
# - считывает файл titanic.csv
# - и расчитывает среднюю арифметическую цену билета (Fare) для каждого класса (Pclass)
# - и сохраняет результирующий датафрейм в файл titanic_mean_fares.csv
def mean_fare_per_class():
    titanic_df = pd.read_csv(get_path('titanic.csv'))
    df = titanic_df.pivot_table(index=['Pclass'],
                                #columns=['Pclass'],
                                values=['Fare'],
                                aggfunc='mean').reset_index()
    df.to_csv(get_path('titanic_mean_fares.csv'))


with DAG(
        dag_id='titanic_dag',
        schedule_interval=None,
        default_args=default_args
) as dag:
    create_titanic_dataset = BashOperator(
        task_id='download_titanic_dataset',
        bash_command='''TITANIC_FILE="titanic-{{ execution_date }}.csv" && \"
        wget https://web.stanford.edu/class/archive/cs/cs109/cs109.1166/stuff/titanic.csv -O ${TITANIC_FILE} && \
        hdfs dfs -mkdir -p /datasets/ && \
        hdfs dfs -put ${TITANIC_FILE} /datasets/ && \
        rm ${TITANIC_FILE} && \
        echo "/datasets/${TITANIC_FILE}"
        ''',
    )
    
    with TaskGroup("prepare_table") as prepare_table:
        drop_hive_table = HiveOperator(
            task_id='drop_hive_table',
            hql='DROP TABLE titanic;',
        )
        
        create_hive_table = HiveOperator(
            task_id='create_hive_table',
            hql='''CREATE TABLE IF NOT EXISTS titanic (Survived INT, Pclass INT, Name STRING, Sex STRING, Age INT,
            Sibsp INT, Parch INT, Fare DOUBLE)
            ROW FORMAT DELIMITED
            FIELDS TERMINATED BY ','
            STORED AS TEXTFILE
            TBLPROPERTIES('skip.header.line.count'='1');
            ''',
        )

        drop_hive_table >> create_hive_table
        
    load_titanic_dataset = HiveOperator(
        task_id='load_data_to_hive',
        hql='''LOAD DATA INPATH "{{ ti.xcom_pull(task_ids='download_titanic_dataset', key='return_value') }}"
        INTO TABLE titanic;''',        
    )
    
    show_avg_fare = BashOperator(
        task_id='show_avg_fare',
        bash_command='''beeline -u jdbs:hive2://localhost:10000 \
        -e "SELECT Pclass, avg(Fare) FROM titanic GROUP BY Pclass;" | tr "\n" ";"''',
    )
    
    def format_message(**kwargs):
        flat_message = kwargs['ti'].xcom_pull(task_ids='show_avg_fare', key='return_value')
        message = flat_message.replace(';', '\n')
        kwargs['ti'].xcom_pull(key='telegram_message', value=message)
        
    prepare_message = PythonOperator(
        task_id='prepare_message',
        python_callable=format_message,
    )
    
    send_result = TelegramOperator(
        task_id='send_success_message_telegram',
        telegram_conn_id='telegram_conn_id',
        chat_id='',
        text='''Pipeline {{ execution_date.int_timestamp }} is done. Result:
        {{ ti.xcom_pull(task_ids='prepare_message', key='telegram_message') }}'''
    )

    create_titanic_dataset >> prepare_table >> load_titanic_dataset >> show_avg_fare >> prepare_message >> send_result