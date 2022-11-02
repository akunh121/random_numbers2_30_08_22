import 'package:cloud_firestore/cloud_firestore.dart';

import 'numbers.dart';

class FirestoreService {
  FirebaseFirestore _db = FirebaseFirestore.instance;

  // Future<void> saveProduct(Product product){
  //   return _db.collection('products').doc(product.productId).set(product.toMap());
  // }

  // Stream<List<int>> getProducts(){
  //   return _db.collection('products').snapshots().map((snapshot) => snapshot.docs.map((document) => Product.fromFirestore(document.data())).toList());
  // }

  Future<void> removeProduct(String productId){
    return _db.collection('products').doc(productId).delete();
  }


}