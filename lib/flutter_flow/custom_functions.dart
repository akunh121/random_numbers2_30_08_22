import 'dart:developer';
import 'dart:math' as math;
import 'dart:math';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'dart:async';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_core/firebase_core.dart';
import 'package:cloud_functions/cloud_functions.dart';
import 'package:firebase_database/firebase_database.dart';
import '../firebase_options.dart';
import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';
import 'package:intl/intl.dart';
import 'package:timeago/timeago.dart' as timeago;

List<int> arrayy = List.empty(growable: true);
Future<List<int>> randomNumbers() async {
// import 'main.dart';
  arrayy.clear();
  var random = math.Random();
  // List<int> array = List.empty(growable: true);

  // for (var i = 1; i < 7; i++) {
  int num = random.nextInt(16273489) + 1;

  //   while (array.contains(num)) {
  //     num = random.nextInt(37) + 1;
  //   }
  //   array.add(num);
  // }
  // array.sort((b, a) => a.compareTo(b));
  List<int> returnNumbers = await creatt(num);
  //  returnNumbers.forEach((k, v) => arrayy.add(int.parse(v)));
  print(returnNumbers);
  // array.add(random.nextInt(7) + 1);
  

  return returnNumbers;
}

//function in dart?

int name() {
  return arrayy[6];
}

Future read() async {
  final ref = FirebaseDatabase.instance.ref();
  final snapshot = await ref.child('loto/assasasasasa').get();
  if (snapshot.exists) {
    print(snapshot.value);
  } else {
    print('No data available.');
  }
}
//  final FirebaseFirestore db;
//who to read one docement from firebase in dart?
// Future creat() async {

//   var snapshot = await db.doc("loto/assasasasasa").get();
//   if (snapshot.exists) {
//     print(snapshot.data());
//   } else {
//     print('No data available.');
//   }
// }
//who to read one docement from firestore in dart?
Future creatt(int index) async {
  arrayy.clear();
  var collection =
      FirebaseFirestore.instance.collection('loto3').doc(index.toString());
  var docSnapshot = await collection.get();
  if (docSnapshot.exists) {
    Map<String, dynamic>? data = docSnapshot.data();
    // var gg = docSnapshot.data();
    // List<int> array = List.empty(growable: true);
    data!.forEach((k, v) => arrayy.add(v));
    print("object");
    //  print(data!['n1']);
    // <-- The value you want to retrieve.
    // Call setState if needed.
    
    return arrayy;
  }
  return;
}
