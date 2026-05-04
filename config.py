"""共用設定 — 擴充美股到 200+ 檔（含成長股、半導體、AI、生技、金融、傳產）"""
UNIVERSE = [
    # ═══ 科技巨頭 / Mega Cap ═══
    "AAPL","MSFT","NVDA","META","GOOGL","GOOG","AMZN","TSLA","AVGO","ORCL",
    "CRM","ADBE","NFLX","INTU","NOW","CSCO","ACN","IBM","UBER","ABNB",
    "PYPL","SHOP","PLTR","SQ","SNOW","DDOG","NET","MDB","TEAM","WDAY",
    "DOCU","ZS","CRWD","PANW","FTNT","OKTA","HUBS","ZM","TWLO","ROKU",
    # ═══ 半導體 ═══
    "TSM","AMD","QCOM","MU","INTC","TXN","AMAT","LRCX","KLAC","ASML",
    "MRVL","NXPI","ADI","MCHP","ON","SWKS","QRVO","WOLF","SMCI","ARM",
    "AI","COIN","HOOD","RDDT","CELH","MSTR","APP","RBLX","DKNG","UPST",
    # ═══ 金融 ═══
    "JPM","BAC","WFC","C","GS","MS","BRK-B","BLK","AXP","V",
    "MA","SCHW","SPGI","CME","ICE","COIN","PYPL","COF","USB","TFC",
    # ═══ 醫療生技 ═══
    "UNH","LLY","JNJ","ABBV","PFE","MRK","TMO","ABT","DHR","BMY",
    "AMGN","GILD","CVS","CI","ELV","ISRG","REGN","VRTX","BIIB","MRNA",
    "ZTS","HUM","BSX","SYK","BDX","EW","HCA","DXCM","IDXX","ILMN",
    # ═══ 消費／零售 ═══
    "WMT","COST","HD","LOW","TGT","NKE","SBUX","MCD","CMG","YUM",
    "DIS","TSLA","TM","F","GM","RIVN","LCID","DASH","UBER","ABNB",
    "BKNG","LULU","DECK","CROX","ULTA","TJX","ROST","BURL","DG","DLTR",
    # ═══ 能源／工業／傳產 ═══
    "XOM","CVX","COP","SLB","EOG","OXY","MPC","PSX","VLO","KMI",
    "CAT","DE","HON","RTX","LMT","BA","GE","NOC","UNP","CSX",
    "NSC","LIN","SHW","FCX","NEM","MMM","DOW","ECL","APD","NUE",
    # ═══ 通訊／民生／媒體 ═══
    "T","VZ","TMUS","CMCSA","NFLX","DIS","PARA","SPOT","WBD","FOX",
    "PG","KO","PEP","PM","MO","CL","KHC","GIS","K","MDLZ",
    # ═══ 房地產／公用事業 ═══
    "AMT","PLD","CCI","O","SPG","WELL","EQIX","DLR","PSA","EXR",
    "NEE","SO","DUK","D","AEP","SRE","XEL","ED","PCG","EXC",
]
# 去重保留順序
UNIVERSE = list(dict.fromkeys(UNIVERSE))

MAX_SLOTS    = 10
STOP_PCT     = 0.20
INITIAL_CASH = 100_000.0
