import 'dart:ffi';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:internet_connection_checker/internet_connection_checker.dart';
import 'package:provider/provider.dart';
import 'firebase_options.dart';
import '../flutter_flow/random_data_util.dart' as random_data;
import 'package:flutter/material.dart';
import '../flutter_flow/flutter_flow_theme.dart';
import '../flutter_flow/flutter_flow_util.dart';
import '../flutter_flow/flutter_flow_widgets.dart';
import '../flutter_flow/custom_functions.dart' as functions;
import 'package:flutter/material.dart';

import 'package:flutter/scheduler.dart';
import 'package:google_fonts/google_fonts.dart';

import 'app_state.dart';
import 'internet_not_connected.dart';

// void main() {
//   runApp(const MyApp());
// }
bool isFirebaseReady = true;
void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  print(44545454545);

  await Firebase.initializeApp(
    options: DefaultFirebaseOptions.currentPlatform,
  ).catchError((e) {
    isFirebaseReady = false;
    print(isFirebaseReady);
    print(e);
  });
  ;
  runApp(MyApp());
}

class MyApp extends StatelessWidget {
  MyApp({Key? key}) : super(key: key);
  final Future<FirebaseApp> _fbApp = Firebase.initializeApp();

  // This widget is the root of your application.
  @override
 Widget build(BuildContext context) {
   
    return StreamProvider<InternetConnectionStatus>(
        initialData: InternetConnectionStatus.connected,
        create: (_) {
          return InternetConnectionChecker().onStatusChange;
        },
       
        child: const MaterialApp(
           debugShowCheckedModeBanner: false,
          title: 'Flutter Demo',
          home: HomePageWidget(),
        ));
  }

  static of(BuildContext context) {}
}

class HomePageWidget extends StatefulWidget {
  const HomePageWidget({Key? key}) : super(key: key);

  @override
  _HomePageWidgetState createState() => _HomePageWidgetState();
}

class _HomePageWidgetState extends State<HomePageWidget> {
  List<int> _randomNumbers = List<int>.empty();
  int lastNum = 0;
  final scaffoldKey = GlobalKey<ScaffoldState>();

  @override
  void initState() {
    super.initState();
    // On page load action.
    SchedulerBinding.instance.addPostFrameCallback((_) async {
      List<int> temp = await functions.randomNumbers();
      temp.removeLast();
      int templast = await functions.name();
      setState(() => lastNum = templast);

      temp.removeLast();

      temp.sort();
      List<int> reversedList = List<int>.from(temp.reversed);
      setState(() => _randomNumbers = reversedList);
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: scaffoldKey,
      appBar: AppBar(
        backgroundColor: FlutterFlowTheme.of(context).primaryColor,
        automaticallyImplyLeading: false,
        title: Text(
          'Random Numbers',
          style: FlutterFlowTheme.of(context).title2.override(
                fontFamily: 'Poppins',
                color: Colors.white,
                fontSize: 22,
              ),
        ),
        actions: [],
        centerTitle: false,
        elevation: 2,
      ),
      body: SafeArea(
        child: GestureDetector(
          onTap: () => FocusScope.of(context).unfocus(),
          child: Column(
            mainAxisSize: MainAxisSize.max,
            children: [
              Expanded(
                flex: 0, // 20%
                 child: Column(
      
          children: <Widget>[
             
            Visibility(
              visible: Provider.of<InternetConnectionStatus>(context) ==
                  InternetConnectionStatus.disconnected,
                   
               child: const InternetNotAvailable(),
            maintainSize: false,
                  maintainAnimation: true,
                  maintainState: true,
                ),
                
                
             
          ],
        ),
              ),
              Expanded(
                child: Padding(
                  padding: EdgeInsetsDirectional.fromSTEB(4, 4, 4, 0),
                  child: Builder(
                    builder: (context) {
                      final num = _randomNumbers
                          .map((e) => formatNumber(
                                e,
                                formatType: FormatType.decimal,
                                decimalType: DecimalType.automatic,
                              ))
                          .toList();
                      return GridView.builder(
                        padding: EdgeInsets.zero,
                        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: 3,
                          crossAxisSpacing: 3,
                          mainAxisSpacing: 5,
                          childAspectRatio: 1,
                        ),
                        scrollDirection: Axis.vertical,
                        itemCount: num.length,
                        itemBuilder: (context, numIndex) {
                          final numItem = num[numIndex];
                          return Container(
                            width: 100,
                            height: 100,
                            decoration: BoxDecoration(
                              gradient: LinearGradient(
                                colors: [
                                  FlutterFlowTheme.of(context).primaryColor,
                                  FlutterFlowTheme.of(context).secondaryColor
                                ],
                                stops: [0, 1],
                                begin: AlignmentDirectional(0, -1),
                                end: AlignmentDirectional(0, 1),
                              ),
                              shape: BoxShape.circle,
                              border: Border.all(
                                width: 5,
                              ),
                            ),
                            alignment: AlignmentDirectional(0, 0),
                            child: Text(
                              numItem,
                              style: FlutterFlowTheme.of(context).bodyText1,
                            ),
                          );
                        },
                      );
                    },
                  ),
                ),
              ),
              
              Padding(
                padding: EdgeInsetsDirectional.fromSTEB(0, 0, 0, 25),
                
                child: Container(
                  width: 125,
                  height: 125,
                  decoration: BoxDecoration(
                    color: Color(0xFFFF0002),
                    shape: BoxShape.circle,
                    border: Border.all(
                      width: 5,
                    ),
                  ),
                  alignment: AlignmentDirectional(0, 0),
                  child: Text(
                    lastNum!=0? lastNum.toString():"loading",
                    style: FlutterFlowTheme.of(context).bodyText1,
                  ),
                ),
              ),
              Padding(
                padding: EdgeInsetsDirectional.fromSTEB(0, 0, 0, 55),
                child: Container(
                  width: 100,
                  height: 100,
                  decoration: BoxDecoration(
                    color: FlutterFlowTheme.of(context).secondaryBackground,
                    boxShadow: [
                      BoxShadow(
                        blurRadius: 3,
                        color: Color(0x33000000),
                        offset: Offset(0, 1),
                      )
                    ],
                    shape: BoxShape.circle,
                  ),
                  
                  child: FFButtonWidget(
                    onPressed: () async {
                      List<int> temp = await functions.randomNumbers();
                      temp.removeLast();
                      int templast = await functions.name();
                      setState(() => lastNum = templast);

                      temp.removeLast();
                      temp.sort();
                      List<int> reversedList = List<int>.from(temp.reversed);
                      setState(() => _randomNumbers = reversedList);
                    },
                    text: 'Button',
                    options: FFButtonOptions(
                      width: 130,
                      color: FlutterFlowTheme.of(context).primaryColor,
                      textStyle:
                          FlutterFlowTheme.of(context).subtitle2.override(
                                fontFamily: 'Poppins',
                                color: Colors.white,
                              ),
                      borderSide: BorderSide(
                        color: Colors.transparent,
                        width: 1,
                      ),
                      borderRadius: BorderRadius.circular(55),
                    ),
                  ),
                  
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
