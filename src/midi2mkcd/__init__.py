import logging

# Library code only logs; the host application decides where records go.
logging.getLogger(__name__).addHandler(logging.NullHandler())
