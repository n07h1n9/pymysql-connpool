"""
author: ljk
email: chaoyuemyself@hotmail.com
"""
import pymysql
import warnings
import queue
import logging

warnings.filterwarnings('error', category=pymysql.err.Warning)
# use logging module for easy debug
logging.basicConfig(format='%(asctime)s %(levelname)8s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger(__name__)
logger.setLevel('WARNING')


class ImprovedDb(object):
    """
    A improved database class based PyMySQL.
    db_config: database config information, should be a dict
    pool: if use connection pool
    pool_init_size: init number of connection pool(default:10)
    """
    def __init__(self, db_config, pool=False, pool_init_size=10):
        self.db_config = db_config
        if pool:
            self.POOL_HARD_LIMIT = 100
            self.pool_init_size = pool_init_size
            self.pool = queue.Queue(self.POOL_HARD_LIMIT)

    def connect(self, cursor=False, dictcursor=False, recreate=False):
        """
        Create and return a MySQL connection object or cursor object.
        cursor: if want cursor object
        dictcursor: cursor type is tuple(default) or dict
        recreate: just a flag indicate if the 'create connection' action due to lack of useable connection in the pool
        """
        try:
            conn = pymysql.connect(**self.db_config)
            if recreate:
                logger.info('create new connection because of pool lacking')
                logger.debug('create new connection because of pool lacking: {}'.format(conn))
            if not cursor:
                return conn
            else:
                return conn.cursor() if not dictcursor else conn.cursor(pymysql.cursors.DictCursor)
        except Exception as err:
            if recreate:
                logger.error('recreate connection error when pool lacking: {}'.format(err))
            else:
                logger.error('create connection error: {}'.format(err))
            raise

    @staticmethod
    def execute_query(connection, query, args=(), dictcursor=False, return_one=False, exec_many=False):
        """
        A higher level implementation for execute query.
        cursor: cursor object of a connection
        return_one: whether want only one row of the result
        exec_many: whether use pymysql's executemany() method
        """
        with connection.cursor() if not dictcursor else connection.cursor(pymysql.cursors.DictCursor) as cur:
            try:
                if exec_many:
                    cur.executemany(query, args)
                else:
                    cur.execute(query, args)
            except Exception:
                raise
            res = cur.fetchall()
        # if no record match the query, return () if return_one==False, else return None
        return (res[0] if res else None) if return_one else res

    def execute_query_multiplex(self, connection, query, args=(), dictcursor=False, return_one=False, exec_many=False):
        """
        A convenience method for:
            connection = self.connect()
            cursor = self.create_cursor(connection)
            self.execute_query(cursor, query, args=())
            cursor.close()
            self.pool_put_connection(connection)
        connection: connection object
        dictcursor: cursor type is tuple(default) or dict
        return_one: whether want only one row of the result
        exec_many: whether use pymysql's executemany() method
        """
        with connection.cursor() if not dictcursor else connection.cursor(pymysql.cursors.DictCursor) as cur:
            try:
                if exec_many:
                    cur.executemany(query, args)
                else:
                    cur.execute(query, args)
            except (pymysql.err.ProgrammingError, pymysql.err.InternalError,
                    pymysql.err.IntegrityError, pymysql.err.NotSupportedError):
                cur.close()
                self.pool_put_connection(connection)
                raise
            except Exception:
                raise
            res = cur.fetchall()
            # return connection back to the pool
            self.pool_put_connection(connection)
        return (res[0] if res else None) if return_one else res

    def create_pool(self):
        """
        Create pool_init_size connections when init the pool.
        """
        logger.debug('init connection pool')
        for i in range(self.pool_init_size):
            conn = self.connect()
            self.pool.put(conn)
        logger.debug('pool object: {}'.format(self.pool.queue))

    def pool_get_connection(self, timeout=3, retry_num=1):
        """
        Multi-thread mode, threads get connection object from the pool.
        If one thread can't get a connection object, then re-create a new connections and put it into pool.
        timeout: timeout of get connection from pool.
            1.If unit task process fast, set a small number(or just ignore it) to take most advantage of the multiplexing;
            2.If unit task may takes long, set a appropriate large number to reduce the errors come from pool lacking,
              and set the 'pool_init_size' in __init__() method as larger as your thread-pool's max_workers(also depend
              the capacity of MySQL)
        retry_num: number of retry when timeout
        """
        try:
            conn = self.pool.get(timeout=timeout)
            logger.debug('get connection: {}'.format(conn))
            # caller should the take care of the availability of the connection object from the pool
            return conn
        except queue.Empty:
            logger.warning('get connection from pool timeout')
            # create new connection at the reason of pool lacking
            conn = self.connect(recreate=True)
            self.pool_put_connection(conn, conn_type='new')
            if retry_num > 0:
                logger.warning('retry get connection from pool')
                return self.pool_get_connection(timeout, retry_num-1)

    def pool_put_connection(self, connection, conn_type='old'):
        """
        Before the sub-thread end, should return the connection object back to the pool
        conn_type: "new" or "old"(default) just a flag can show more information
        """
        try:
            logger.debug('return connection: {}'.format(connection))
            self.pool.put_nowait(connection)
        except queue.Full:
            if conn_type == 'new':
                logger.warning("can't put new connection to pool")
            else:
                logger.warning("can't put old connection back to pool")

    @staticmethod
    def db_close(connection):
        connection.close()
        logger.debug("close connection: {}".format(connection))
