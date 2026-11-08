#property strict
void OnStart(){
 string names[]={"EURUSD#","USDJPY#","GBPUSD#"};
 int out=FileOpen("DS_probe.csv",FILE_WRITE|FILE_CSV|FILE_ANSI,',');
 FileWrite(out,"symbol","period","server_first","terminal_first","last","bars","maxbars");
 for(int k=0;k<3;k++){
  SymbolSelect(names[k],true);
  ENUM_TIMEFRAMES p[]={PERIOD_M1,PERIOD_M5,PERIOD_M15};
  for(int j=0;j<3;j++){
   MqlRates rates[];
   for(int retry=0;retry<120;retry++){
    ResetLastError();int n=CopyRates(names[k],p[j],D'1970.01.01',TimeCurrent(),rates);
    if(n>0)break;
    Sleep(500);
   }
   FileWrite(out,names[k],EnumToString(p[j]),TimeToString((datetime)SeriesInfoInteger(names[k],p[j],SERIES_SERVER_FIRSTDATE),TIME_DATE|TIME_MINUTES),TimeToString((datetime)SeriesInfoInteger(names[k],p[j],SERIES_FIRSTDATE),TIME_DATE|TIME_MINUTES),TimeToString((datetime)SeriesInfoInteger(names[k],p[j],SERIES_LASTBAR_DATE),TIME_DATE|TIME_MINUTES),Bars(names[k],p[j]),TerminalInfoInteger(TERMINAL_MAXBARS));
   Print("DS probe ",names[k]," ",EnumToString(p[j])," bars ",Bars(names[k],p[j]));
  }
 }
 FileClose(out);
}
