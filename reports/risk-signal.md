# Risk signal tabulation

Fraud prevalence per band of the amount, hour and velocity features, in the training and validation periods. The test period is not read. A feature carries marginal signal only if prevalence moves clearly across its bands and in the same direction in both periods.

| Period | Transactions | Fraud | Prevalence |
|---|---|---|---|
| train | 2,628,068 | 2,640 | 0.100 % |
| validation | 738,013 | 730 | 0.099 % |

## amount_usd

Amount in US dollars; the bands are training deciles.

| Band | Train rows | Train fraud | Train prevalence | Validation rows | Validation fraud | Validation prevalence |
|---|---|---|---|---|---|---|
| below 107.29 | 262,781 | 269 | 0.102 % | 73,446 | 81 | 0.110 % |
| 107.29 to below 197.07 | 262,816 | 251 | 0.096 % | 73,658 | 70 | 0.095 % |
| 197.07 to below 286.81 | 262,788 | 273 | 0.104 % | 73,876 | 74 | 0.100 % |
| 286.81 to below 376.70 | 262,826 | 275 | 0.105 % | 73,668 | 69 | 0.094 % |
| 376.70 to below 466.65 | 262,805 | 292 | 0.111 % | 74,299 | 76 | 0.102 % |
| 466.65 to below 883.74 | 262,817 | 260 | 0.099 % | 73,986 | 69 | 0.093 % |
| 883.74 to below 1,604.88 | 262,811 | 281 | 0.107 % | 74,468 | 86 | 0.115 % |
| 1,604.88 to below 2,976.07 | 262,810 | 256 | 0.097 % | 73,502 | 60 | 0.082 % |
| 2,976.07 to below 5,120.64 | 262,807 | 248 | 0.094 % | 73,933 | 85 | 0.115 % |
| 5,120.64 or more | 262,807 | 235 | 0.089 % | 73,177 | 60 | 0.082 % |

## hour

Hour of the day.

| Band | Train rows | Train fraud | Train prevalence | Validation rows | Validation fraud | Validation prevalence |
|---|---|---|---|---|---|---|
| 0 | 109,041 | 114 | 0.105 % | 30,724 | 41 | 0.133 % |
| 1 | 109,717 | 99 | 0.090 % | 30,849 | 29 | 0.094 % |
| 2 | 109,575 | 110 | 0.100 % | 30,919 | 27 | 0.087 % |
| 3 | 109,544 | 111 | 0.101 % | 30,603 | 29 | 0.095 % |
| 4 | 109,201 | 123 | 0.113 % | 30,642 | 22 | 0.072 % |
| 5 | 109,220 | 106 | 0.097 % | 30,696 | 27 | 0.088 % |
| 6 | 109,718 | 103 | 0.094 % | 30,627 | 44 | 0.144 % |
| 7 | 109,825 | 127 | 0.116 % | 30,728 | 28 | 0.091 % |
| 8 | 109,490 | 114 | 0.104 % | 30,591 | 34 | 0.111 % |
| 9 | 109,907 | 135 | 0.123 % | 30,808 | 29 | 0.094 % |
| 10 | 109,231 | 102 | 0.093 % | 30,821 | 29 | 0.094 % |
| 11 | 109,696 | 110 | 0.100 % | 30,692 | 32 | 0.104 % |
| 12 | 109,165 | 125 | 0.115 % | 30,838 | 30 | 0.097 % |
| 13 | 109,950 | 113 | 0.103 % | 30,725 | 29 | 0.094 % |
| 14 | 109,474 | 117 | 0.107 % | 30,817 | 24 | 0.078 % |
| 15 | 109,186 | 94 | 0.086 % | 30,953 | 32 | 0.103 % |
| 16 | 109,531 | 99 | 0.090 % | 30,568 | 21 | 0.069 % |
| 17 | 109,913 | 98 | 0.089 % | 30,694 | 29 | 0.094 % |
| 18 | 110,396 | 97 | 0.088 % | 30,660 | 38 | 0.124 % |
| 19 | 108,849 | 99 | 0.091 % | 30,627 | 25 | 0.082 % |
| 20 | 109,697 | 105 | 0.096 % | 30,675 | 38 | 0.124 % |
| 21 | 108,752 | 114 | 0.105 % | 30,944 | 37 | 0.120 % |
| 22 | 109,715 | 101 | 0.092 % | 31,144 | 29 | 0.093 % |
| 23 | 109,275 | 124 | 0.113 % | 30,668 | 27 | 0.088 % |

## tx_count_24h

The customer's transactions in the 24 hours before.

| Band | Train rows | Train fraud | Train prevalence | Validation rows | Validation fraud | Validation prevalence |
|---|---|---|---|---|---|---|
| below 1 | 2,525,890 | 2,547 | 0.101 % | 709,522 | 704 | 0.099 % |
| 1 to below 2 | 99,579 | 93 | 0.093 % | 27,826 | 26 | 0.093 % |
| 2 to below 3 | 2,535 | 0 | 0.000 % | 653 | 0 | 0.000 % |
| 3 or more | 64 | 0 | 0.000 % | 12 | 0 | 0.000 % |

## tx_count_7d

The customer's transactions in the 7 days before.

| Band | Train rows | Train fraud | Train prevalence | Validation rows | Validation fraud | Validation prevalence |
|---|---|---|---|---|---|---|
| below 1 | 2,023,703 | 2,059 | 0.102 % | 567,729 | 552 | 0.097 % |
| 1 to below 3 | 593,658 | 573 | 0.097 % | 167,191 | 173 | 0.103 % |
| 3 to below 6 | 10,702 | 8 | 0.075 % | 3,091 | 5 | 0.162 % |
| 6 or more | 5 | 0 | 0.000 % | 2 | 0 | 0.000 % |

## seconds_since_previous

Seconds since the customer's previous transaction.

| Band | Train rows | Train fraud | Train prevalence | Validation rows | Validation fraud | Validation prevalence |
|---|---|---|---|---|---|---|
| below 3,600 | 4,427 | 3 | 0.068 % | 1,277 | 1 | 0.078 % |
| 3,600 to below 86,400 | 97,751 | 90 | 0.092 % | 27,214 | 25 | 0.092 % |
| 86,400 to below 604,800 | 502,185 | 488 | 0.097 % | 141,793 | 152 | 0.107 % |
| 604,800 or more | 1,889,202 | 1,929 | 0.102 % | 567,717 | 552 | 0.097 % |
| no earlier transaction | 134,503 | 130 | 0.097 % | 12 | 0 | 0.000 % |
